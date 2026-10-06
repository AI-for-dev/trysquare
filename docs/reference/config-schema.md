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

**No environment variable** changes what is measured. The previous tool read ten, and one
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

## `[isolation]`

What each run executes inside. A property of the machine, like `workdir`, and recorded
on every run it measured.

```toml
[isolation]
backend = "none"
```

`backend` defaults to `none`: the agent sees whatever the operator sees, the other runs
included, and the synthesis header says so. An unknown backend, or a setting the backend
does not take, is refused when the file is loaded.

```toml
[isolation]
backend = "docker"
cpus = 2          # optional
memory = "4g"     # optional
```

`docker` runs each run in its own container, from the image the scenario declares in
`[agent] image`. The container sees the run's clone and session, read-write, and the
bricks it loads, read-only, each at the path the host has it. Nothing else of the
workdir, the sources or the output directory exists inside. It runs as your uid, so
what the agent writes stays yours, and it gets a fresh home of its own.

That home holds what `pi` needs from your `~/.pi/agent` and nothing more:

- `models.json` with only the providers the scenario calls: the agent's, and each
  judge's. Each holds a placeholder where its key was and a relay's address where the
  provider's was. See below.
- `settings.json` with `defaultThinkingLevel` only, the level a subagent thinks at. The
  rest of your settings would be inherited from the machine, which no scenario says.
- never `auth.json`, the tokens of `/login`.

`models` names another file to take the providers from, relative to this config:

```toml
[isolation]
backend = "docker"
models = "models.json"   # optional, instead of ~/.pi/agent/models.json
```

Your own `models.json` serves `pi` when you use it yourself, where a key written out or
a `!command` is fine. A file of its own for the sandbox holds only `$NAME` references, so
it carries no secret and can sit in the repository beside this config. A file `models`
names that does not exist is refused before any run. The `none` backend takes no
`models`: there, `pi` reads your own.

**The provider's key never enters the container.** Its `apiKey` and header values in
that `models.json` must read the secret from a variable set where trysquare runs
(`$NAME`, `${NAME}`, or `Bearer ${NAME}`). The agent gets a placeholder instead, and each
provider's address becomes a relay of its own, which trysquare runs on your machine for
the launch. A relay swaps the placeholder for the key on each request and sends it on, over HTTPS, to
the provider's host and no other; a request without the placeholder is refused, and an
error in which the provider quotes the key comes back with the placeholder in its place.
Before any run, a launch refuses a key written out in the file, a `!command` (it would
run inside the container), a variable that is unset, and a variable `env` still passes
in, where the agent could read it beside the placeholder.

A variable unset where trysquare runs can come from a `.env` file instead, so the key
need not be exported before each launch:

```toml
[isolation]
backend = "docker"
secrets = "~/.config/trysquare/secrets.env"   # optional, relative to this config
```

A variable set in the environment wins over the file. The file holds one `NAME=value`
per line, with an optional `export` prefix and optional quotes; blank lines and `#`
comments are skipped, and nothing in it is expanded. It is read at most once per
launch, when the relays are prepared, and its values stay in memory: never written to
disk, to `state.json` or to a log. A file that is missing, a line that is not an
assignment, or a variable it sets to nothing stops the launch, naming the file and the
line number, never the line. Its variables only feed the relays: `env` does not read
the file, so nothing in it enters the sandbox.

The relay listens on docker's bridge, the address `host.docker.internal` reaches from a
container, so nothing off the machine can call it. The agent needs no network of its own
to reach its provider, so no proxy variable has to be passed in either.

`env` lists the variables passed into the container, by name, for whatever else the
agent needs. Nothing else of your environment goes in, and a variable `env` names that
is unset is refused before any run.

:::{warning}
Only a provider your `models.json` describes, with its `baseUrl`, goes through the relay.
One of `pi`'s own that it does not describe reads its key from the environment, so from
`env`, and **the agent can read that key**. Describe the provider in `models.json` to
keep it out.
:::

trysquare ships `image/Dockerfile`: Ubuntu 24.04 pinned by digest, Node checked
against its published checksum, pi at an exact version, and git, python3 and fd. Build
it, extend it with what your repository's tests need, and name the result in the
scenario:

```bash
docker build -t trysquare-agent image/
```

```dockerfile
FROM trysquare-agent
RUN apt-get update && apt-get install -y --no-install-recommends make \
    && rm -rf /var/lib/apt/lists/*
```

`cpus` and `memory` hold each container to that much of the machine, swap included.
They slow a run down, so like `concurrency` they are written in `state.json` and in the
synthesis header, and `compare` prints them as a difference. Without them a container
may use all of the machine.

A launch refuses when docker is not running, when the image is not on the machine
(nothing is pulled on a run's behalf), or when `pi --version` does not run in it. A
container whose client was killed is removed; one left by a `kill -9` of trysquare
itself is not, and `docker ps --filter name=trysquare-` finds it.

```toml
[isolation]
backend = "bwrap"
bind = ["~/.nvm"]   # optional
```

`bwrap` runs each run in a [bubblewrap](https://github.com/containers/bubblewrap)
sandbox, on Linux only, with no daemon and no image: the agent uses this machine's
tools, so `[agent] image` is not read and the launch says so. The sandbox starts from an
empty root and sees `/usr`, `/bin`, `/sbin`, `/lib`, `/lib64` and `/etc` read-only, the
run's clone and session read-write and its bricks read-only at the paths the host has
them, and nothing else: no `/home`, no other run under `/tmp`. `bind` adds paths
read-only, for a `pi` or a toolchain installed outside the system directories. The home,
the relay, `env` and their refusals are the docker backend's, with the relay on the
loopback the sandbox shares, and a variable reaches the sandbox through its environment,
never its command line. It runs as your uid and dies with trysquare, so no agent
outlives an interrupt.

A launch refuses when bwrap cannot make a sandbox here: bubblewrap missing, or
unprivileged user namespaces forbidden, as some distributions and most containers do.

## Absent config

Not an error. A scenario that names no logical repository needs nothing resolved, and
the built-in defaults apply. A config given explicitly with `--config` that does not
exist *is* an error.
