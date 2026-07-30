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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import agent as agent_mod
from . import repo as repo_mod
from . import validation as validation_mod
from .config import CONFIG_NAME, Config
from .measure import EMPTY, VALID, Run, merge
from .outputs import Output, slug
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
) -> Plan:
    """Turns a scenario into a concrete plan, refusing what cannot be measured."""
    overrides = overrides or {}
    repetitions = overrides.get("repetitions") or scenario.protocol["repetitions"]
    output = Output(output_root, scenario, repetitions)

    notes = []
    for key, value in sorted(overrides.items()):
        declared = scenario.protocol.get(key, scenario.agent.get(key))
        notes.append(f"OVERRIDE: {key} {declared} -> {value}")

    # A repository entry may be a directory or a URL. Either way this only computes
    # where it will be read from; nothing is cloned or reached for until `execute`.
    url = config.remote(scenario.task["repo"])
    if url:
        repo_path = source_dir(config, scenario.task["repo"], url, scenario.task["etalon"])
        repo_source = url
    else:
        repo_path = config.repo(scenario.task["repo"])
        repo_source = str(repo_path)

    # Every brick must be where the scenario says, checked before anything is
    # spent. A missing brick used to surface as a validator failure after a matrix
    # had been paid for, or worse, as a prompt path silently sent to the agent as
    # literal text.
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

    # Reading state is fine here; writing is not. `resolve` must leave the disk
    # untouched so `--dry-run` cannot create a directory - or worse, reset the
    # ledger of an experiment that already exists. Preparing and writing belong to
    # `execute`, which is the part that actually spends something.
    state = output.load_or_create_state(overrides) if resume else output.initial_state(overrides)
    todo = output.to_do(state, only)

    if only:
        notes.append(
            f"INCOMPLETE: only {', '.join(only)} will run "
            f"({len(todo)} of {len(output.plan())}); no synthesis is written"
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
    )


def interleave(todo: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """Orders runs by repetition first, so cells are measured side by side.

    Grouping by cell would measure the first cell under an idle provider and the
    last under a loaded one, and the duration column would then compare our own
    scheduling rather than the configurations.
    """
    return sorted(todo, key=lambda item: (item[1]["repetition"], item[1]["cell"]))


def brick_paths(scenario: Scenario, config: Config, cell: Cell, base: Path) -> dict:
    """The bricks one cell loads, resolved to real paths."""
    wanted = cell.delta.get("harness", [])
    extensions: list[Path] = []
    agents: list[Path] = []
    skills: list[Path] = []
    agent_model = None

    for name in wanted:
        brick = scenario.bricks.get(name)
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

        for path in brick.get("paths", []):
            resolved = (base / path).resolve()
            if name == "skills":
                skills.append(resolved)
            else:
                agents.append(resolved)
        if brick.get("model"):
            agent_model = brick["model"]

    return {
        "extensions": extensions,
        "agents": agents,
        "skills": skills,
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
                f"check network access, or list what the remote has: "
                f"git ls-remote --tags {url}"
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
    import subprocess

    if not (clone_dir / "package.json").is_file():
        return
    proc = subprocess.run(
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
    string "bricks/vague-ticket.md" sent to the agent as its task, and the runs
    looked entirely normal while measuring nothing.
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
        checked whatever they look like - otherwise a command written `neon.py`,
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
    one frozen run must not take the matrix with it.
    """
    scenario = plan.scenario
    base = scenario.path.parent if scenario.path else Path.cwd()
    cell = scenario.cell(meta["cell"])
    run = Run(id=run_id, cell=cell.name, repetition=meta["repetition"])

    try:
        bricks = brick_paths(scenario, plan.config, cell, base)

        prompt = read_brick(base, cell.delta.get("prompt") or scenario.task.get("prompt")) or ""
        context = read_brick(base, cell.delta.get("context"))
        system = read_brick(base, cell.delta.get("system"))
        thinking = cell.delta.get("thinking") or scenario.agent["thinking"]

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
        outcome, tries = agent_mod.run_until_productive(clone, args, timeout, attempts)

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
            run.detail = agent_mod.first_error(outcome.stream) or outcome.stderr[:200] or "no tokens consumed"
            return run

        # The trace is kept next to the run, not in the measured repository.
        trace = work / "trace.jsonl"
        trace.write_text(outcome.stream)

        prompt_file = work / "prompt.txt"
        prompt_file.write_text(prompt)

        # The agent's final prose, extracted once here so no validator has to
        # reimplement stream parsing to get at it.
        from .measure import final_text

        response_file = work / "response.txt"
        response_file.write_text(final_text(outcome.stream))

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
            )
            if validator.mode == "script":
                result = validation_mod.run_script(validator, context_file, timeout, cwd=base)
            elif validator.mode == "judge":
                result = judge(plan, validator, directory, base, prompt, outcome, clone, attempts)
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
    except Exception as e:  # noqa: BLE001 - one run must not cost the matrix
        run.state = EMPTY if run.state == VALID and not run.usage else run.state
        run.detail = f"{type(e).__name__}: {e}"
    return run


JUDGE_BRICK = "bricks/judge-tool.ts"


def judge(
    plan: Plan,
    validator,
    directory: Path,
    base: Path,
    prompt: str,
    outcome,
    clone: Path,
    attempts: int,
):
    """Assembles the judge's dossier and runs it.

    The pieces are whatever the scenario declared, and nothing else - certainly not
    the cell. `response` is the agent's final prose rather than its transcript: a
    judge asked whether a note is usable must score the note, not the work behind it.
    """
    from . import measure as measure_mod

    rubric_path = validator.config.get("rubric")
    rubric = read_brick(base, rubric_path) or ""

    available = {
        "prompt": prompt,
        "response": measure_mod.final_text(outcome.stream),
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
    brick = Path(__file__).resolve().parent.parent / JUDGE_BRICK
    if not brick.is_file():
        return validation_mod.Result(validator.mode, None, detail=f"judge brick missing: {brick}")

    work, judge_prompt = validation_mod.judge_dossier(
        directory / "judge", validator, rubric, pieces
    )
    timeout = plan.overrides.get("timeout") or plan.scenario.protocol.get(
        "timeout", plan.config.fallback("timeout")
    )
    return validation_mod.run_judge(validator, work, judge_prompt, brick, timeout, attempts)


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
    """
    directory = plan.output.run_dir(run_id)
    (directory / "diff.patch").write_text(repo_mod.diff(clone))
    plan.output.write_configuration(
        run_id,
        {
            "cell": cell.name,
            "repo": plan.repo_source,
            "etalon": plan.scenario.task["etalon"],
            "etalon_commit": repo_mod.commit_of(plan.repo_path, plan.scenario.task["etalon"]),
            "provider": plan.scenario.agent["provider"],
            "model": plan.scenario.agent["model"],
            "thinking": thinking,
            "injected": prepared.injected,
            # Two places may declare a subagent's model, so the trace settles
            # which one applied.
            "agents": prepared.agents,
        },
    )


def execute(plan: Plan, on_run=None) -> list[Run]:
    """Runs the plan, writing state as it goes so an interruption is resumable.

    The repository is pinned **first**, before a single directory is created. After
    `output.prepare()` an unreachable URL would leave behind an experiment directory
    holding a ledger of runs that never had a repository to run against; before it, the
    refusal reaches the operator and the disk is as untouched as after a dry run.
    """
    prepare_source(plan.config, plan.scenario.task["repo"], plan.scenario.task["etalon"])
    plan.output.prepare()
    state = plan.output.load_or_create_state(plan.overrides)
    plan.output.write_state(state)
    concurrency = plan.overrides.get("concurrency") or plan.scenario.protocol.get(
        "concurrency", plan.config.fallback("concurrency")
    )

    done: list[Run] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(one_run, plan, rid, meta): rid for rid, meta in plan.todo}
        for future in futures:
            run = future.result()
            done.append(run)
            plan.output.record(state, run.id, run)
            plan.output.write_state(state)
            if on_run:
                on_run(run)

    existing = {r.id: r for r in plan.output.read_measures()}
    for run in done:
        existing[run.id] = run
    plan.output.write_measures(list(existing.values()))
    plan.output.summarise(state)
    plan.output.write_state(state)
    return done
