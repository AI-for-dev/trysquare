# SPDX-License-Identifier: BSD-3-Clause
"""Orchestrating a matrix: what runs, in what order, and what is written down.

Runs are **interleaved** across cells rather than grouped by cell. That is not a
scheduling detail: interleaved runs see the same provider load, which is the only
reason durations are comparable between cells of one matrix. They are never
comparable between matrices, and nothing here pretends otherwise.

An exception in one run must not cost the matrix. A missing cell beats a lost
table, and the runs already paid for are the ones being protected.
"""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from . import agent as agent_mod
from . import interrupt
from . import repo as repo_mod
from . import validation as validation_mod
from .config import CONFIG_NAME, Config, closest
from .measure import EMPTY, VALID, Run, counted, merge, models, one_line
from .outputs import RESUMABLE, Carried, Output, carryable, matrices, slug, write_text
from .scenario import Cell, Scenario


@dataclass
class Plan:
    """Everything settled before a single token is spent."""

    scenario: Scenario
    config: Config
    output: Output

    # The local directory runs clone from. For a URL this does not exist until
    # `execute` or `replay` has pinned it, which is deliberate: `resolve` must not touch
    # the network any more than it touches the disk, or `--dry-run` stops being free.
    repo_path: Path

    # What the config declared - the URL, or the path. For display and for the archive:
    # a pinned directory under `$TMPDIR` tells an operator nothing about what is being
    # measured, and tells a reader six months later even less.
    repo_source: str

    todo: list[tuple[str, dict]]
    overrides: dict
    blindness: dict
    notes: list[str]

    # The runs of a lower matrix of this same experiment that this launch is to carry.
    # `resolve` decides it; `execute` is what copies anything.
    carried: Carried | None = None

    # The cells whose results this launch discards and measures again, as `--overwrite
    # CELL` named them. `execute` needs them too: the ledger it writes is loaded from
    # disk, so it is the one that has to record those cells as measured afresh.
    replay: tuple[str, ...] = ()

    @property
    def runs(self) -> int:
        return len(self.todo)


def resolve(
    scenario: Scenario,
    config: Config,
    output_root: Path,
    overrides: dict | None = None,
    only: tuple[str, ...] = (),
    resume: bool = False,
    grouped: bool | None = None,
    extend: bool = False,
    replay: tuple[str, ...] = (),
) -> Plan:
    """Turns a scenario into a concrete plan, refusing what cannot be measured."""
    overrides = overrides or {}
    repetitions = overrides.get("repetitions") or scenario.protocol["repetitions"]
    output = Output(output_root, scenario, repetitions, grouped=grouped)

    notes = []
    for key, value in sorted(overrides.items()):
        declared = scenario.protocol.get(key, scenario.agent.get(key))
        notes.append(f"OVERRIDE: {key} {declared} -> {value}")

    repo_path, repo_source = settle_repo(scenario, config)
    refuse_unmeasurable(scenario)

    # Reading state is fine here; writing is not. `resolve` must leave the disk
    # untouched so `--dry-run` cannot create a directory - or worse, reset the
    # ledger of an experiment that already exists. Preparing and writing belong to
    # `execute`, which is the part that actually spends something.
    # A cell name that matches nothing would otherwise be *filtered*, not refused:
    # `--only` with a typo in it ran zero runs and looked like an experiment with
    # nothing left to do.
    names = [cell.name for cell in scenario.cells]
    refuse_unknown_cells("--only", only, names)
    refuse_unknown_cells("--overwrite", replay, names)
    # Both restrict the launch to the cells they name, so giving each of them a list says
    # it twice - and a precedence rule between two lists is the kind nobody can see.
    if only and replay:
        raise RuntimeError(
            "--overwrite already names the cells to measure: "
            + ", ".join(repr(c) for c in replay)
            + "\n--only would name them a second time. Drop one of the two."
        )

    # Read here, and once, before the state is loaded: `load_or_create_state` fills in
    # the ids of every cell the ledger does not know, so a comparison made against its
    # result would have nothing left to report.
    previous = output.read_state()

    # Only into a matrix that holds nothing yet. A carry is how a matrix *begins* as the
    # extension of a lower one; once it has a ledger of its own, `--resume` is what fills
    # it, and looking again would let a second launch re-import what the first carried.
    carried = (
        None
        if previous.get("runs")
        else carryable(output_root, scenario, output.plan(), repetitions)
    )
    if extend and not previous.get("runs") and (carried is None or carried.mismatch):
        raise RuntimeError(refuse_carry(carried, output_root, scenario, repetitions))
    if not extend and carried:
        notes.append(available_note(carried))

    # What a replay gives up, said before it is spent. Only when there is something to
    # give up: on a matrix that holds nothing, nothing is measured *again* and nothing is
    # kept - the note below, which counts what this launch leaves alone, is the true one.
    if replay and previous.get("runs"):
        measured_again = [m for m in previous["runs"].values() if m["cell"] in replay]
        kept = len(previous["runs"]) - len(measured_again)
        notes.append(
            f"REPLAY: {counted(len(measured_again), 'run')} of {', '.join(replay)} are "
            f"measured again; the {counted(kept, 'run')} of the other cells of "
            f"{output.directory.name} are kept"
        )
    elif not resume and previous.get("runs"):
        leftovers = sum(1 for m in previous["runs"].values() if m["state"] in RESUMABLE)
        if leftovers:
            notes.append(
                f"OVERWRITE: {output.directory.name} exists, {leftovers} of its runs "
                f"produced nothing. Relaunching resets the whole ledger; --resume "
                f"relaunches only those {leftovers}"
            )
        else:
            notes.append(
                f"OVERWRITE: {output.directory.name} already holds a finished "
                f"experiment. Relaunching overwrites it - the archive of previous "
                f"versions is git"
            )
    notes.extend(drift_notes(output, previous))
    # A replay keeps the ledger and discards the named cells from it. `initial_state` stays
    # the whole-matrix answer: it is what makes an overwrite carry nothing.
    if replay:
        state = output.replayed(output.load_or_create_state(overrides), replay)
    elif resume:
        state = output.load_or_create_state(overrides)
    else:
        state = output.initial_state(overrides)
    # Before the check below, deliberately: a carried run is a measured run, so a cell
    # rewritten since the lower matrix measured it must condemn the carry exactly as it
    # condemns a resume. Seeding first is what puts it in reach of that refusal.
    if extend and carried:
        state = output.seed(state, carried)

    # A cell renamed is a new cell and measures itself. A cell rewritten under the same
    # name is the defect the directory name refuses one level up, and only the ledger
    # can catch it: nothing in the name changes when a delta does.
    # A replay is in reach of it too: `output.replayed` has already freed the cells it
    # names, so what is left are the cells this launch *keeps* - whose old results would
    # be published beside the new ones.
    changed = output.changed_cells(state) if resume or replay else []
    if changed:
        raise RuntimeError(refuse_changed(output, changed, replay))

    todo = output.to_do(state, only or replay)

    # From the ledger and not from the flag, so a matrix that was extended says so on the
    # launch that extended it *and* on every launch after it. An overwrite carries nothing
    # and `initial_state` has no such record, which is the right answer rather than a gap.
    for record in state.get("carried", ()):
        notes.append(
            f"CARRIED: {counted(record['runs'], 'run')} measured in {record['from']} at "
            f"{counted(record['repetitions'], 'repetition')} (concurrency "
            f"{record['concurrency']}, "
            f"timeout {record['timeout']}s) are this matrix's own runs, carried and never "
            f"re-measured. The synthesis says so too"
        )
    if extend and carried and carried.stranded:
        notes.append(
            f"STRANDED: {counted(carried.stranded, 'run')} of {carried.directory.name} its "
            f"ledger counts and its measures.json has no row for did not travel, and will "
            f"be measured here instead"
        )

    if only:
        notes.append(
            f"INCOMPLETE: only {', '.join(only)} will run "
            f"({len(todo)} of {len(output.plan())}); no synthesis is written"
        )

    # Read from the state and not from what was on disk, so it also covers the replay of a
    # cell of a matrix that holds nothing yet: five sixths of it never launched is the same
    # incompleteness whether or not a previous launch is what left it that way. Over the
    # cells the scenario declares, like every other count - a cell it dropped keeps its
    # runs and holds nothing incomplete.
    if replay:
        outstanding = sum(
            1
            for m in state["runs"].values()
            if m["cell"] in names and m["cell"] not in replay and m["state"] in RESUMABLE
        )
        if outstanding:
            notes.append(
                f"INCOMPLETE: the cells this launch keeps still hold "
                f"{counted(outstanding, 'run')} with no result, and a replay leaves them "
                f"alone; --resume measures them and the synthesis follows"
            )

    return Plan(
        scenario=scenario,
        config=config,
        output=output,
        repo_path=repo_path,
        repo_source=repo_source,
        todo=interleave(todo),
        overrides=overrides,
        blindness=validation_mod.blindness(scenario),
        notes=notes,
        carried=carried if extend else None,
        replay=replay,
    )


def refuse_unknown_cells(flag: str, given: tuple[str, ...], names: list[str]) -> None:
    """Refuses a cell name no cell of the scenario answers to, whichever flag gave it.

    The refusal lists every cell, because a grid names its cells by joining axis values
    and the exact spelling is easier to copy than to guess.
    """
    unknown = [c for c in given if c not in names]
    if unknown:
        raise RuntimeError(
            f"{flag} names no cell of this scenario: "
            + ", ".join(repr(c) + closest(c, names) for c in unknown)
            + f"\nCells: {', '.join(names)}"
        )


def refuse_changed(output: Output, changed: list[str], replay: tuple[str, ...]) -> str:
    """Why a launch that keeps results cannot keep these ones.

    Two wordings for one defect, because the way out differs: a resume keeps every cell,
    so any of them may be the one rewritten; a replay already discards the cells it names,
    so a cell reported here is one it was told to keep. Both end on the same offer -
    measure those cells again, and keep the rest of the matrix - which is what
    `--overwrite CELL` is for.
    """
    kept = (
        "a replay keeps every cell it does not name"
        if replay
        else "--resume keeps every run that produced a result"
    )
    return (
        "these cells changed since their runs were measured: "
        + ", ".join(changed)
        + f"\n{kept}, so completing this matrix would publish two configurations under "
        "one cell name.\n"
        "Rename the cell, so the new one is measured as itself and the old runs keep "
        "their own name; or measure them again and keep the rest of "
        f"{output.directory.name}:\n"
        + "  --overwrite "
        + " --overwrite ".join(f'"{name}"' for name in changed)
    )


def available_note(carried: Carried) -> str:
    """What a launch says about a lower matrix it is *not* carrying.

    The only place `--extend` is discoverable at the moment it would be useful. An
    operator who has just asked for twenty repetitions is about to pay for ten they
    already own, and nothing else in the output would tell them.
    """
    if carried.mismatch:
        return (
            f"NOT CARRYABLE: {carried.directory.name} is this experiment's matrix at "
            f"{carried.repetitions} repetitions, but it was measured with a different "
            f"{', '.join(carried.mismatch)}, so its runs are not runs of this experiment"
        )
    return (
        f"AVAILABLE: {carried.directory.name} holds {counted(len(carried), 'measured run')} "
        f"this matrix asks for at its first {carried.repetitions} repetitions. --extend "
        f"carries them over and measures only the difference; without it they are measured "
        f"again"
    )


def refuse_carry(carried: Carried | None, root: Path, scenario: Scenario, repetitions: int) -> str:
    """Why `--extend` has nothing to work with, naming what is actually on disk.

    Refused rather than quietly measured from scratch, for the reason `--only` with a typo
    is refused: a flag that silently does nothing looks like a flag that worked.
    """
    if carried is not None:
        return (
            f"--extend: {carried.directory.name} was measured with a different "
            f"{', '.join(carried.mismatch)}. Neither is in a directory name - it carries "
            f"the scenario, the etalon, the provider and the model - so two matrices can "
            f"share a name without sharing what they measured, and carrying across that "
            f"would publish measurements of something else"
        )
    found = sorted(matrices(root, scenario))
    return (
        f"--extend: no matrix of this experiment under "
        f"{counted(repetitions, 'repetition')} in {root}"
        + (f", only n{', n'.join(str(n) for n in found)}" if found else ", and none at all")
        + ".\nExtending carries the runs of the same experiment measured fewer times; "
        "there are none, and a plain run measures this matrix from scratch"
    )


def drift_notes(output: Output, previous: dict) -> list[str]:
    """What an existing ledger and the scenario disagree about, before the first token.

    Adding a cell to a published matrix already works: the ledger gains the new ids as
    missing, and a resume relaunches only what produced nothing. Nothing said so, and a
    saving nobody announced is a saving nobody takes - the safe-looking move was to
    remeasure a matrix that was already paid for.

    Neither note depends on `--resume`: both state what a resume would do, so they are
    as true when the flag is absent as when it is there.
    """
    if not previous.get("runs"):
        return []
    added, stale = output.cell_drift(previous)
    # Of the cells this scenario still declares, so the count reads against the matrix
    # about to be planned. Counting the stale ones too made the two numbers in the note
    # contradict each other whenever both notes fired.
    measured = sum(
        1
        for m in previous["runs"].values()
        if m["state"] not in RESUMABLE and m["cell"] not in stale
    )
    notes = []
    if added:
        notes.append(
            f"ADDED: the scenario declares {', '.join(added)}, which "
            f"{output.directory.name} does not know. --resume measures "
            f"{counted(sum(added.values()), 'run')} and leaves the "
            f"{counted(measured, 'run')} that already produced a result untouched; "
            f"relaunching without it measures all {len(output.plan())}"
        )
    if stale:
        notes.append(
            f"STALE: {output.directory.name} holds {', '.join(stale)}, which the "
            f"scenario no longer declares. Those {counted(sum(stale.values()), 'run')} "
            f"stay in every table - a cell must never vanish silently - and none is "
            f"launched or counted, since the scenario has no such cell. Delete the "
            f"directory to publish the new matrix alone"
        )
    return notes


def settle_repo(scenario: Scenario, config: Config) -> tuple[Path, str]:
    """Where the repository will be read from, and what the config declared.

    A repository entry may be a directory or a URL. Either way this only computes
    where it will be read from; nothing is cloned or reached for until `execute`.
    """
    url = config.remote(scenario.task["repo"])
    if url:
        return source_dir(config, scenario.task["repo"], url, scenario.task["etalon"]), url
    path = config.repo(scenario.task["repo"])
    return path, str(path)


def refuse_unmeasurable(scenario: Scenario) -> None:
    """The refusals that spend nothing, shared by `resolve` and `validate`.

    Every brick must be where the scenario says, checked before anything is
    spent. A missing brick used to surface as a validator failure after a matrix
    had been paid for, or worse, as a prompt path silently sent to the agent as
    literal text.
    """
    base = scenario.path.parent if scenario.path else Path.cwd()
    missing = preflight(scenario, base)
    if missing:
        raise RuntimeError(
            "these files the scenario references do not exist:\n  "
            + "\n  ".join(missing)
            + "\nPaths are relative to the scenario file."
        )

    # A subagent's thinking level cannot be declared anywhere, so it is verified.
    uses_subagents = "agents" in scenario.bricks
    refusal = validation_mod.check_thinking_precondition(
        scenario.agent.get("thinking"),
        agent_mod.ambient_thinking(),
        uses_subagents,
    )
    if refusal:
        raise RuntimeError(refusal)


def interleave(todo: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """Orders runs by repetition first, so cells are measured side by side.

    Grouping by cell would measure the first cell under an idle provider and the
    last under a loaded one, and the duration column would then compare our own
    scheduling rather than the configurations.
    """
    return sorted(todo, key=lambda item: (item[1]["repetition"], item[1]["cell"]))


# Shipped inside the package, beside the code that loads it, for the reason
# `validation.JUDGE_BRICK` gives: an installed wheel has no repository around it, and
# neither brick is a per-experiment choice.
AGENT_GATE = Path(__file__).resolve().parent / "agent-gate.ts"


def brick_paths(scenario: Scenario, config: Config, cell: Cell, base: Path) -> dict:
    """The bricks one cell loads, resolved to real paths."""
    extensions: list[Path] = []
    agents: list[Path] = []
    skills: list[Path] = []
    files: dict[str, Path] = {}
    agent_model = None

    for name, brick in scenario.declared(cell)["harness"].items():
        if brick is None:
            raise RuntimeError(f"cell {cell.name!r} wants brick {name!r}, which is not declared")

        if "repo" in brick:
            # A pinned repository: cloned once at its tag, so every cell loads the
            # same pinned state - an experiment that pins the measured repository
            # and lets the harness float is measuring the operator.
            clone_dir = prepare_harness(config, brick["repo"], brick["tag"])
            extensions.append(clone_dir / brick.get("load", "."))
        elif "load" in brick:
            extensions.append((base / brick["load"]).resolve())

        # `kind` says what the paths are; the name only decides for the historical
        # brick called `skills`, kept so existing scenarios keep meaning what they
        # said. Several skill bricks can then coexist, each cited by name in a
        # variant's `harness` list.
        kind = brick.get("kind") or ("skills" if name == "skills" else "agents")

        # A `files` brick puts its material in the measured tree, keyed by where it
        # lands. Two bricks of one cell aiming at the same destination are refused
        # rather than resolved by declaration order: which of the two files the agent
        # would have read is a question no reader of the scenario could answer.
        if kind == "files":
            for destination, source in brick.get("files", {}).items():
                if destination in files:
                    raise RuntimeError(
                        f"cell {cell.name!r} gives {destination!r} twice, from two "
                        f"bricks - the second is {name!r}"
                    )
                files[destination] = (base / source).resolve()
            continue

        for path in brick.get("paths", []):
            resolved = (base / path).resolve()
            if kind == "skills":
                skills.append(resolved)
            else:
                agents.append(resolved)
        if brick.get("model"):
            agent_model = brick["model"]

    # A cell that injects subagents loads the gate, always, without declaring it.
    #
    # Dropping agent definitions into the clone does not make them the only ones
    # reachable: the subagent tool takes its scope as a parameter the model chooses,
    # and the default reaches the library's own built-in agents - none of which
    # declares a model, so each would inherit whatever this machine defaults to. A
    # cell injecting `explorer` would therefore have measured someone else's agent
    # on someone else's settings, and nothing in the output would have said so.
    #
    # `check_agent_models` already refuses an injected agent with no model. The gate
    # closes the sibling hole, and it is not optional for the same reason: a
    # scenario that forgot to declare it would measure something other than what it
    # says. Left to a `[harness]` entry, forgetting it was one line away.
    if agents:
        extensions.append(AGENT_GATE)

    return {
        "extensions": extensions,
        "agents": agents,
        "skills": skills,
        "files": files,
        "agent_model": agent_model,
    }


PATH_SUFFIXES = (".md", ".txt", ".json", ".toml")


def looks_like_path(value: str) -> bool:
    return "/" in value or value.endswith(PATH_SUFFIXES)


_HARNESS_LOCK = threading.Lock()
_SOURCE_LOCK = threading.Lock()
READY = ".trysquare-ready"


def source_dir(config: Config, name: str, url: str, etalon: str) -> Path:
    """Where a remote repository is pinned. A pure path computation, no disk.

    Keyed by **tag**, like `harness/{name}-{tag}`, and that is what deletes the entire
    staleness question: a directory already there is by construction already at the tag
    being asked for. Nothing to refetch, nothing to verify, and a tag moved upstream
    cannot leak into a matrix in flight. The cost is re-cloning when a matrix changes
    etalon, which is the right trade against a cache-invalidation state machine.

    Keyed by a hash of the URL as well, so editing the URL in the config lands somewhere
    else instead of silently reusing the previous repository's clone - the same class of
    defect as reusing a half-written harness.
    """
    digest = hashlib.blake2s(url.encode(), digest_size=4).hexdigest()
    return config.workdir() / "sources" / f"{slug(name)}-{digest}-{slug(etalon)}"


def prepare_source(config: Config, name: str, etalon: str) -> Path:
    """The local repository runs clone from, pinning a remote exactly once.

    Called from `execute` before anything is written, so an unreachable URL costs a
    refusal and an untouched disk instead of a ledger full of empty measures; and again
    from `one_run`, so no caller can reach a clone without having pinned first. On the
    hit path that second call is one lock and one `is_file()` against a run that lasts
    minutes.

    Serialised for the reason `prepare_harness` documents: cells run concurrently and
    every one of them arrives here at the same moment. The readiness marker records the
    URL as well as the tag, so a directory named after a hash can say what it is, and a
    marker left by a different URL forces a fresh clone rather than being trusted.
    """
    url = config.remote(name)
    if url is None:
        path = config.repo(name)
        if not path.exists():
            raise repo_mod.RepoError(
                f"[repos] {name} resolves to {path}, which does not exist. "
                f"Fix it in {config.path or CONFIG_NAME}"
            )
        return path

    target = source_dir(config, name, url, etalon)
    stamp = f"{name}@{etalon}\n{url}\n"
    with _SOURCE_LOCK:
        marker = target / READY
        if marker.is_file() and marker.read_text() == stamp:
            return target
        try:
            repo_mod.pin(url, etalon, target)
        except repo_mod.RepoError as e:
            raise repo_mod.RepoError(
                f"could not clone [repos] {name} at etalon {etalon!r}\n"
                f"  url: {url}\n"
                f"  git: {e.detail or e}\n"
                f"Nothing was measured. Fix the entry in {config.path or CONFIG_NAME}, "
                f"check network access, or see what the remote has: "
                + (
                    # A commit is not a ref, so `ls-remote` cannot answer for it. What
                    # can is a clone, which is also what would have to succeed here.
                    f"git clone {url} /tmp/x && git -C /tmp/x cat-file -e {etalon}"
                    if repo_mod.is_commit(etalon)
                    else f"git ls-remote --tags {url}"
                )
            ) from e
        marker.write_text(stamp)
    return target


def prepare_harness(config: Config, name: str, tag: str) -> Path:
    """Clones a pinned harness repository once, and installs it, exactly once.

    Serialised, because cells run concurrently and every cell that loads this brick
    reaches here at the same moment. Checking `exists()` and then cloning is not
    atomic: the first real run of a scenario with four concurrent cells had two of
    them fail on `destination path already exists`, and a third succeeded only by
    winning the race - which meant it may have loaded an extension whose
    dependencies were never installed.

    A readiness marker rather than mere existence, so a clone left half-written by
    an interrupted attempt is redone instead of silently reused. Reusing a partial
    harness is the kind of failure that produces a plausible measurement.

    A `[harness]` entry may be a URL, exactly like a `[repos]` entry: the two sections
    have the same shape, and one accepting an address the other refuses would be an
    asymmetry nobody could guess. Nothing clones *from* this directory - it is loaded as
    an extension - so it keeps `--no-tags`.
    """
    clone_dir = config.workdir() / "harness" / f"{slug(name)}-{slug(tag)}"
    with _HARNESS_LOCK:
        if (clone_dir / READY).is_file():
            return clone_dir
        source = config.harness_remote(name) or config.harness_repo(name)
        repo_mod.clone(source, tag, clone_dir)
        install_dependencies(clone_dir)
        (clone_dir / READY).write_text(f"{name}@{tag}\n")
    return clone_dir


def install_dependencies(clone_dir: Path, timeout: int = 300) -> None:
    """Installs a harness repository's runtime dependencies, once per pinned clone.

    A freshly cloned extension is not loadable as it stands: `pi-subagent` declares
    `@earendil-works/pi-coding-agent` as a runtime dependency, and the agent resolves
    imports from `node_modules` next to the extension or above it. Without this the
    extension fails to load, and a cell that was supposed to measure a toolkit
    measures its absence.

    A missing `package.json` is not an error: a brick may be a single file.
    """
    if not (clone_dir / "package.json").is_file():
        return
    proc = interrupt.run(
        ["npm", "install", "--silent", "--omit=dev"],
        cwd=clone_dir,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"could not install dependencies for {clone_dir.name}: "
            f"{(proc.stderr or proc.stdout).strip()[:300]}"
        )


def read_brick(base: Path, value: str | None) -> str | None:
    """A scenario value that may be inline text or a path to a file.

    A value that looks like a path and does not exist **raises**. It used to fall
    back to being treated as inline text, and that silent fallback is exactly the
    defect this project keeps paying for: a mistyped prompt path became the literal
    string "tickets/vague.md" sent to the agent as its task, and the runs looked
    entirely normal while measuring nothing.
    """
    if not value:
        return None
    candidate = (base / value).resolve()
    if candidate.is_file():
        return candidate.read_text()
    if looks_like_path(value):
        raise RuntimeError(f"referenced file does not exist: {candidate} (declared as {value!r})")
    return value


def referenced_paths(scenario: Scenario, base: Path) -> list[tuple[str, Path]]:
    """Every file the scenario points at, with where it was declared.

    Collected so a missing brick is refused **before the first token**, not
    discovered by a validator failure after a matrix has been paid for.
    """
    out: list[tuple[str, Path]] = []

    def add(label: str, value, always: bool = False) -> None:
        """`always` for keys that are paths by definition.

        A validator command and a rubric are never inline text, so they must be
        checked whatever they look like - otherwise a command written `score.py`,
        with no separator to give it away, slips past preflight and only fails once
        a run has been paid for.
        """
        if isinstance(value, str) and (always or looks_like_path(value)):
            out.append((label, (base / value).resolve()))

    add("task.prompt", scenario.task.get("prompt"))
    if scenario.hypothesis:
        add("scenario.hypothesis", scenario.hypothesis, always=True)

    # These three may legitimately be inline text, so the heuristic applies.
    for cell in scenario.cells:
        for key in ("prompt", "context", "system"):
            add(f"cell {cell.name!r} -> {key}", cell.delta.get(key))

    for validator in scenario.validators:
        add(f"validation[{validator.mode}].command", validator.config.get("command"), always=True)
        add(f"validation[{validator.mode}].rubric", validator.config.get("rubric"), always=True)

    for name, brick in scenario.bricks.items():
        if not isinstance(brick, dict):
            continue
        if "repo" not in brick:
            add(f"harness.{name}.load", brick.get("load"), always=True)
        for path in brick.get("paths", []):
            add(f"harness.{name}.paths", path, always=True)
    return out


def preflight(scenario: Scenario, base: Path) -> list[str]:
    """Refuses a scenario whose bricks are not where it says they are."""
    missing = [
        f"{label}: {path}" for label, path in referenced_paths(scenario, base) if not path.exists()
    ]
    return missing


def one_run(plan: Plan, run_id: str, meta: dict) -> Run:
    """Measures one cell once, and writes down everything about it.

    Every failure path here ends in a `Run` with a state, never in an exception:
    one frozen run must not take the matrix with it. A stop is the exception to that,
    and `interrupt.Stopped` is shaped so it cannot be caught here - see below.
    """
    # The only flag test outside `interrupt`, and it earns its place: a worker released
    # from `_HARNESS_LOCK` at the wrong moment would otherwise get as far as clearing a
    # directory and cloning into it before its first child refused.
    if interrupt.stopping():
        raise interrupt.Stopped(interrupt.signalled())

    scenario = plan.scenario
    base = scenario.path.parent if scenario.path else Path.cwd()
    cell = scenario.cell(meta["cell"])
    run = Run(id=run_id, cell=cell.name, repetition=meta["repetition"])

    try:
        bricks = brick_paths(scenario, plan.config, cell, base)

        declared = scenario.declared(cell)
        prompt = read_brick(base, declared["prompt"]) or ""
        context = read_brick(base, declared["context"])
        system = read_brick(base, declared["system"])
        thinking = declared["thinking"]

        # Pinned here as well as in `execute`, so a clone cannot be reached without it.
        # Idempotent: on the hit path this is one lock and one `is_file()`.
        source = prepare_source(plan.config, scenario.task["repo"], scenario.task["etalon"])

        work = plan.config.workdir() / plan.output.directory.name / run_id
        clone = repo_mod.clone(source, scenario.task["etalon"], work / "repo")
        prepared = repo_mod.Prepared(path=clone, etalon=scenario.task["etalon"])
        repo_mod.inject(
            prepared,
            context=context,
            system=system,
            agents=bricks["agents"],
            skills=bricks["skills"],
            files=bricks["files"],
            agent_model=bricks["agent_model"],
        )
        repo_mod.check_agent_models(prepared.agents)

        session_dir = work / "session"
        # What is already there belongs to an earlier launch: the work directory is
        # keyed by the run id, which is stable, so a resume finds the previous
        # measurement's sessions still in place. Noted now so the archive can keep this
        # launch's and only this launch's.
        earlier = {p.name for p in session_dir.glob("*.jsonl")} if session_dir.is_dir() else set()

        args = agent_mod.argv(
            prompt=prompt,
            provider=scenario.agent["provider"],
            model=scenario.agent["model"],
            thinking=thinking,
            session_dir=session_dir,
            extensions=bricks["extensions"],
            skills=bricks["skills"],
            has_context=context is not None,
        )
        timeout = plan.overrides.get("timeout") or scenario.protocol.get(
            "timeout", plan.config.fallback("timeout")
        )
        attempts = scenario.protocol.get("attempts", plan.config.fallback("attempts"))
        # Kept next to the run rather than in the measured repository, and written by
        # the agent as it goes rather than by the harness afterwards: a stream nobody
        # bounds must never become an object here.
        trace = work / "trace.jsonl"
        ceiling = stream_ceiling(plan)
        outcome, tries = agent_mod.run_until_productive(
            clone, args, timeout, attempts, trace, ceiling
        )

        run.usage = outcome.usage
        run.duration = outcome.duration
        run.attempts = tries

        # Archived before the emptiness test, and that ordering is the point. A run that
        # produced nothing leaves the session as its only evidence, and it is exactly the
        # run somebody will want to read. One file per attempt, so the count matches
        # `run.attempts`.
        plan.output.archive_sessions(run_id, session_dir, exclude=earlier)

        if not outcome.produced_something:
            run.state = EMPTY
            # The ceiling first. When the harness is what ended the run, that is the
            # reason, and an error the truncated stream happens to carry is not.
            run.detail = (
                one_line(outcome.stderr)[:200]
                if outcome.overflowed
                else outcome.error or one_line(outcome.stderr)[:200] or "no tokens consumed"
            )
            return run

        prompt_file = work / "prompt.txt"
        prompt_file.write_text(prompt)

        # The agent's final prose, read once with the rest of the stream so no
        # validator has to reimplement stream parsing to get at it.
        response_file = work / "response.txt"
        response_file.write_text(outcome.response)

        # Computed once here rather than by every validator that wants them. Reading the
        # changed files writes to the clone's *index*, which is safe because each run owns
        # its clone; the working tree is untouched, because the harness archives the diff
        # after validation and a stray file would land there as the agent's work.
        touched = repo_mod.changed_files(clone)
        at_etalon = repo_mod.etalon_files(source, scenario.task["etalon"])

        results = []
        for validator in scenario.validators:
            blind = validator.mode == "judge"
            directory = plan.output.run_dir(run_id) / "validation" / validator.mode
            context_file = validation_mod.write_context(
                directory,
                repo=clone,
                etalon=scenario.task["etalon"],
                etalon_checkout=source,
                prompt_file=prompt_file,
                session_dir=session_dir,
                trace=trace,
                cell=cell.name,
                repetition=meta["repetition"],
                blind=blind,
                response_file=response_file,
                test_command=scenario.task.get("test_command"),
                prepare=list(scenario.task.get("prepare", ())),
                artefacts=list(scenario.task.get("artefacts", ())),
                touched=touched,
                files=at_etalon,
                given=prepared.given,
                declared=validator.metrics,
            )
            if validator.mode == "script":
                result = validation_mod.run_script(validator, context_file, timeout, cwd=base)
            elif validator.mode == "judge":
                result = judge(
                    plan,
                    validator,
                    directory,
                    base,
                    prompt,
                    outcome.response,
                    clone,
                    attempts,
                    work,
                )
            else:
                # Recorded as a failure rather than silently skipped: an
                # unimplemented validator must not read as a verdict.
                result = validation_mod.Result(
                    validator.mode, None, detail=f"{validator.mode} validator not implemented"
                )
            plan.output.write_validation(run_id, validator.mode, result.payload, result.stderr)
            results.append((validator.mode, result.payload))
            if not result.ok and result.detail:
                run.detail = result.detail

        metrics, reasons, state, detail = merge(results, scenario.declared_metrics)
        run.metrics, run.reasons = metrics, reasons
        if state != VALID:
            run.state, run.detail = state, detail or run.detail

        archive(plan, run_id, clone, prepared, cell, thinking)
    # Deliberately unable to reach `interrupt.Stopped`, which is a `KeyboardInterrupt`.
    # A cancelled run recorded here would be recorded as a *result*: with tokens already
    # consumed it stays `valid`, and `valid` is not resumable, so the run would be out of
    # reach of every later `--resume`. A stop leaves no row at all, and stays `missing`.
    except Exception as e:  # noqa: BLE001 - one run must not cost the matrix
        run.state = EMPTY if run.state == VALID and not run.usage else run.state
        run.detail = one_line(f"{type(e).__name__}: {e}")
    return run


def judge(
    plan: Plan,
    validator,
    directory: Path,
    base: Path,
    prompt: str,
    response: str,
    clone: Path,
    attempts: int,
    work: Path,
):
    """Assembles the judge's dossier and runs it.

    The pieces are whatever the scenario declared, and nothing else - certainly not
    the cell. `response` is the agent's final prose rather than its transcript: a
    judge asked whether a note is usable must score the note, not the work behind it.

    `work` is where the judge's own stream goes. It is passed rather than derived from
    `directory`, which lies inside the published archive: a 16 MB stream per run has no
    business in a tree meant to be committed, and `--extend` copies that tree whole.
    """
    rubric_path = validator.config.get("rubric")
    rubric = read_brick(base, rubric_path) or ""

    available = {
        "prompt": prompt,
        "response": response,
        "diff": repo_mod.diff(clone),
    }
    declared = validator.config.get("pieces") or list(available)
    unknown = [p for p in declared if p not in available]
    if unknown:
        return validation_mod.Result(
            validator.mode, None, detail=f"unknown judge pieces: {', '.join(unknown)}"
        )
    pieces = {name: available[name] for name in declared}

    # The brick ships with the tool, so it is resolved against the package rather
    # than the scenario: it is not a per-experiment choice.
    brick = validation_mod.JUDGE_BRICK
    if not brick.is_file():
        return validation_mod.Result(validator.mode, None, detail=f"judge brick missing: {brick}")

    dossier, judge_prompt = validation_mod.judge_dossier(
        directory / "judge", validator, rubric, pieces
    )
    timeout = plan.overrides.get("timeout") or plan.scenario.protocol.get(
        "timeout", plan.config.fallback("timeout")
    )
    return validation_mod.run_judge(
        validator,
        dossier,
        judge_prompt,
        brick,
        timeout,
        work / "judge.jsonl",
        attempts,
        stream_ceiling(plan),
    )


def stream_ceiling(plan: Plan) -> int:
    """How many bytes one agent run may write, from a config expressed in megabytes.

    Megabytes in the file a human writes, bytes at the one place that compares a size,
    so the unit lives where it is read rather than in every signature it passes.
    """
    return plan.config.fallback("stream_limit") * interrupt.MEGABYTE


def recorded_model(sessions: list[Path]) -> str | None:
    """The model the archived sessions say answered, or None when they do not say.

    None rather than the declared pattern: an archive that cannot name the model must
    say so, and filling the gap with the intention is how a fallback would hide.
    """
    if not sessions:
        return None
    text = "\n".join(p.read_text(errors="replace") for p in sessions)
    seen = models(text)
    return seen[-1] if seen else None


def archive(plan: Plan, run_id: str, clone: Path, prepared, cell: Cell, thinking: str) -> None:
    """Keeps the sources a re-score needs, and nothing more.

    The raw stream is almost entirely streaming deltas, which teach nothing the
    per-message record does not: 15.9 MB of stream against 30 KB of session. What
    is archived is the tag, the diff and the configuration, which is exactly what
    `replay` needs to reconstitute a tree.

    The repository and the commit its tag resolved to are recorded here. Without them a
    published archive cannot say *what* it measured - and with a URL the address is the
    only thing that identifies it. The commit also closes a hole that predates remotes:
    a local repository whose tag was moved between two matrices left no trace at all.

    `model_id` is that same distinction one level up. `model` is a **pattern** the agent
    resolves against what the provider offers - `gemma-4` runs as `gemma-4-31b` - so the
    declared value is an intention and only the session says what answered. Keeping the
    pattern alone left the archive unable to name the model it measured, and made a
    fallback to the machine's `defaultModel` indistinguishable from a resolution.
    """
    directory = plan.output.run_dir(run_id)
    write_text(directory / "diff.patch", repo_mod.diff(clone))
    plan.output.write_configuration(
        run_id,
        {
            "cell": cell.name,
            "repo": plan.repo_source,
            "etalon": plan.scenario.task["etalon"],
            "etalon_commit": repo_mod.commit_of(plan.repo_path, plan.scenario.task["etalon"]),
            "provider": plan.scenario.agent["provider"],
            "model": plan.scenario.agent["model"],
            "model_id": recorded_model(plan.output.sessions(run_id)),
            "thinking": thinking,
            "injected": prepared.injected,
            # What the task was handed, as opposed to what the harness hid from git.
            # A patch touching one of these paths is the agent editing material it was
            # given, and a reader cannot tell that from the patch alone.
            "given": prepared.given,
            # Two places may declare a subagent's model, so the trace settles
            # which one applied.
            "agents": prepared.agents,
        },
    )


def unconsumed(futures: dict, already: set[str]) -> list[Run]:
    """The runs that had finished while the loop was not looking.

    `as_completed` hands runs over one at a time, so an interrupt in the loop body
    abandons every run that finished behind the one being written down. They were paid
    for at the same price as the one that got recorded.

    The order of the tests is not free. `exception()` raises on a cancelled future, so
    cancellation is checked first; and a future carrying a `Stopped` is a run that was
    cut short, which is the one thing that must never be written down.
    """
    out = []
    for future, run_id in futures.items():
        if future.cancelled() or not future.done() or run_id in already:
            continue
        if future.exception() is not None:
            continue
        out.append(future.result())
    return out


def execute(plan: Plan, on_run=None) -> list[Run]:
    """Runs the plan, writing state and measures as it goes so an interruption is resumable.

    The two are written **together**, run by run - see `keep`, which is also where the
    order of the two writes is argued. Writing the ledger alone was enough to resume and
    not enough to keep what had been paid for: a Ctrl-C left runs marked `valid` in
    `state.json` with no row in `measures.json`, and those runs were then out of reach -
    `--resume` relaunches only what produced nothing, and `replay` has no row to
    re-score. The matrix went on to publish as complete over fewer runs than were
    measured, and nothing in the output said so.

    An interrupt keeps everything that was finished and records nothing that was not:
    what had completed unseen is harvested on the way out, what was still running is
    left `missing` for the next `--resume`.

    The repository is pinned **first**, before a single directory is created. After
    `output.prepare()` an unreachable URL would leave behind an experiment directory
    holding a ledger of runs that never had a repository to run against; before it, the
    refusal reaches the operator and the disk is as untouched as after a dry run.
    """
    prepare_source(plan.config, plan.scenario.task["repo"], plan.scenario.task["etalon"])
    plan.output.prepare()
    # Before the ledger is loaded, because the carry writes one: from here on this matrix
    # holds the carried runs as its own, and everything below reads them like any other.
    if plan.carried:
        plan.output.absorb(plan.carried, plan.overrides)
    state = plan.output.load_or_create_state(plan.overrides)
    # The ledger comes off the disk, so this is where a replay is written down: the cells
    # being measured again lose the results they had and take today's declaration, or the
    # next `--resume` would refuse the runs this launch is about to measure.
    if plan.replay:
        state = plan.output.replayed(state, plan.replay)
    plan.output.write_state(state)
    concurrency = plan.overrides.get("concurrency") or plan.scenario.protocol.get(
        "concurrency", plan.config.fallback("concurrency")
    )

    # Runs are consumed as they finish, not in the order they were submitted. Walking
    # the futures in submission order made one slow run hold back the ledger of every
    # run that finished behind it: an interruption lost work that was done, and a
    # counter watching this loop would sit still and then jump by a whole batch.
    order = {rid: i for i, (rid, _) in enumerate(plan.todo)}
    done: list[Run] = []

    # Where each row goes, decided before any run finishes, because rows are now written
    # many times. The row order of `measures.json` is not cosmetic: `verdict.gap_interval`
    # resamples with `random.choices`, which draws **by index**, so two identical matrices
    # whose rows landed in a different order would publish different bounds under the same
    # fixed seed. Rows already archived keep their place and this pass follows in plan
    # order - the order runs complete in is a race, the order they were planned in is not.
    archived = {r.id: r for r in plan.output.read_measures()}
    place = {run_id: i for i, run_id in enumerate(archived)}
    for run_id, _ in plan.todo:
        place.setdefault(run_id, len(place))

    recorded: set[str] = set()

    def keep(run: Run) -> None:
        """Writes one run down, in both files, once.

        The measures first and the ledger second, because an interrupt can land between
        them and the two leftovers are not equally bad. A row with the ledger still
        saying `missing` is relaunched by the next `--resume` and overwritten in place,
        which costs one run. A ledger saying `valid` with no row is a run nobody can
        reach at all: `--resume` skips what produced something and `replay` has no row
        to re-score. Both files are written to a neighbour and renamed, so neither is
        ever half of a new one.

        Once, because `record` accumulates attempts across launches: a run written down
        twice would say it had been measured twice.
        """
        if run.id in recorded:
            return
        recorded.add(run.id)
        archived[run.id] = run
        plan.output.record(state, run.id, run)
        plan.output.write_measures(sorted(archived.values(), key=lambda r: place[r.id]))
        plan.output.write_state(state)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(one_run, plan, rid, meta): rid for rid, meta in plan.todo}
        try:
            for future in as_completed(futures):
                run = future.result()
                done.append(run)
                keep(run)
                if on_run:
                    on_run(run)
        except BaseException:
            # Three things, and none of them replaces another. `cancel_futures` drops
            # what has not started, because leaving the `with` otherwise **runs the
            # whole queue**: the shutdown sentinel goes in behind every pending work
            # item, so a matrix stopped at its fifth run went on spending for another
            # hour with nothing printed. `stop` is for the runs already in flight, which
            # no cancellation can reach: their children die and their next child is
            # refused, and without it the wait below is the longest run's full timeout.
            # The harvest is for the runs that were already finished, which cost exactly
            # what the recorded one cost.
            interrupt.stop()
            pool.shutdown(wait=False, cancel_futures=True)
            for run in unconsumed(futures, recorded):
                keep(run)
            # So an interrupted ledger describes itself rather than keeping whatever a
            # previous launch left in `complete`.
            plan.output.summarise(state)
            plan.output.write_state(state)
            raise

    done.sort(key=lambda r: order.get(r.id, len(order)))
    plan.output.summarise(state)
    plan.output.write_state(state)
    return done
