"""The command line.

Seven subcommands, and `--output` roots every one of them that writes.

Overrides are always **announced at launch**. That is not politeness: the previous
tool had a protocol declared in a document and defaults in the code that
contradicted it, and the code wins at the moment somebody types the command, so a
published matrix was measured at the wrong load. A plan that cannot be executed is
not a plan.

Overrides are then stamped according to their effect. Anything that changes what is
measured goes into the directory name, so a quick run at three repetitions writes
elsewhere and cannot corrupt a published matrix at ten. Anything that changes the
load goes into `state.json` and the synthesis header, because it conditions the
retry count and therefore every cost column.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import agent as agent_mod
from . import config as config_mod
from . import measure as measure_mod
from . import parity as parity_mod
from . import progress as progress_mod
from . import repo as repo_mod
from . import runner as runner_mod
from . import table as table_mod
from . import validation as validation_mod
from .measure import EMPTY, Run, VALID
from .outputs import SESSION, Output, incomplete_note
from .scenario import ScenarioError, load as load_scenario

# Overrides that change what is measured. They enter the directory name, so they
# cannot overwrite another experiment's results.
STAMPED = ("repetitions",)
# Overrides that change the load. Same directory, recorded in the state and header.
RECORDED = ("concurrency", "timeout")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except (ScenarioError, config_mod.ConfigError, repo_mod.RepoError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        # A refused precondition: a real message, not a traceback.
        print(f"refused: {e}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trysquare",
        description="A scenario harness for measuring coding agents reproducibly.",
    )
    sub = parser.add_subparsers(dest="command")

    def with_common(p, output_required=True):
        p.add_argument("scenario", help="path to a scenario TOML file")
        p.add_argument(
            "--output",
            "-o",
            required=output_required,
            type=Path,
            help="directory every output is written under",
        )
        p.add_argument("--config", type=Path, help="config file (default: nearest trysquare.toml)")
        return p

    def with_progress(p):
        # Per subcommand rather than on the top-level parser: with subparsers a
        # global flag has to precede the subcommand, which reads backwards and is
        # typed wrong.
        p.add_argument(
            "--no-progress",
            action="store_true",
            help=f"never draw the live bar (also: {progress_mod.OFF}=1)",
        )
        return p

    run = with_progress(with_common(sub.add_parser("run", help="measure a scenario")))
    run.add_argument("--repetitions", type=int, help="override, stamped into the directory name")
    run.add_argument("--concurrency", type=int, help="override, recorded in the state")
    run.add_argument("--timeout", type=int, help="override, recorded in the state")
    run.add_argument(
        "--only", action="append", default=[], help="restrict to these cells (repeatable)"
    )
    run.add_argument("--resume", action="store_true", help="fill only what produced nothing")
    run.add_argument("--dry-run", action="store_true", help="show the plan and spend nothing")
    run.set_defaults(func=cmd_run)

    validate = sub.add_parser(
        "validate", help="check a scenario end to end, without an output directory or a token"
    )
    validate.add_argument("scenario", help="path to a scenario TOML file")
    validate.add_argument(
        "--config", type=Path, help="config file (default: nearest trysquare.toml)"
    )
    validate.set_defaults(func=cmd_validate)

    render = with_progress(
        with_common(sub.add_parser("render", help="rebuild tables from stored measures"))
    )
    render.add_argument("--repetitions", type=int, help="which matrix to read")
    render.add_argument("--reference", help="score against another cell, without remeasuring")
    render.add_argument(
        "--html",
        action="store_true",
        help="also export each archived session to HTML, in its own run directory",
    )
    render.set_defaults(func=cmd_render)

    replay = with_progress(
        sub.add_parser("replay", help="re-score archived runs without spending tokens")
    )
    replay.add_argument(
        "directory", type=Path, help="an experiment directory, or one run inside it"
    )
    replay.add_argument("--config", type=Path)
    replay.add_argument("--scenario", type=Path, required=True)
    replay.add_argument(
        "--rescore",
        action="store_true",
        help="also re-run the script validators and rewrite measures.json and the synthesis",
    )
    replay.set_defaults(func=cmd_replay)

    compare = sub.add_parser("compare", help="compare two experiments")
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)
    compare.set_defaults(func=cmd_compare)

    parity = sub.add_parser("parity", help="check this harness against the previous bench")
    parity.add_argument(
        "measures", nargs="?", type=Path, help="the bench's published measures JSON"
    )
    parity.add_argument("--archive", type=Path, help="the bench's archived run directories")
    parity.add_argument("--reference", default="base")
    parity.add_argument("--criterion", default="overflow")
    parity.add_argument(
        "--smoke", type=Path, help="an experiment directory: run layer 4's mechanical checks"
    )
    parity.add_argument("--workdir", type=Path, help="where sessions live, for the thinking check")
    parity.add_argument("--config", type=Path)
    parity.set_defaults(func=cmd_parity)

    form = with_common(sub.add_parser("form", help="generate or ingest a manual scoring form"))
    form.add_argument("--ingest", type=Path, help="merge a filled form back in")
    form.set_defaults(func=cmd_form)

    return parser


# --- run -------------------------------------------------------------------


def _load(args):
    """The scenario and the config it will run under, from one command's arguments.

    The config is searched from the scenario's own directory, not the operator's:
    an experiment carried to another machine must keep resolving against the
    `trysquare.toml` that sits beside it.
    """
    scenario = load_scenario(args.scenario)
    config = config_mod.load(args.config, start=Path(args.scenario).resolve().parent)
    return scenario, config


def collect_overrides(args) -> dict:
    out = {}
    for key in (*STAMPED, *RECORDED):
        value = getattr(args, key, None)
        if value is not None:
            out[key] = value
    return out


def cmd_run(args) -> int:
    scenario, config = _load(args)
    overrides = collect_overrides(args)

    plan = runner_mod.resolve(
        scenario,
        config,
        args.output,
        overrides=overrides,
        only=tuple(args.only),
        resume=args.resume,
    )

    print(f"{scenario.title or scenario.name}")
    print(f"  {len(scenario.cells)} cells x {plan.output.repetitions} repetitions")
    print(f"  etalon {scenario.task['etalon']} of {plan.repo_source}")
    if plan.repo_source != str(plan.repo_path):
        # A URL. Announcing only the pinned directory would show the operator a path
        # under $TMPDIR, which says nothing about what is about to be measured.
        print(f"  pinned at {plan.repo_path} (cloned on the first run)")
    print(f"  output {plan.output.directory}")
    for note in plan.notes:
        print(f"  ! {note}")
    for line in _blindness_lines(plan):
        print(line)
    print(f"  {plan.runs} runs to perform")

    if not plan.todo:
        print("  nothing to do: every run already produced a result")
        return 0

    if args.dry_run:
        for run_id, meta in plan.todo[:12]:
            print(f"    {run_id}  {meta['cell']}  #{meta['repetition']}")
        if plan.runs > 12:
            print(f"    ... and {plan.runs - 12} more")
        print("\n  dry run: nothing was spent")
        return 0

    if not agent_mod.available():
        print(f"error: {agent_mod.PI!r} is not on PATH", file=sys.stderr)
        return 1

    # The bar opens here rather than above: the plan header, the dry run and the
    # missing-binary refusal all print and return before a single run exists to
    # count. It closes before the synthesis, which is not part of the count either.
    enabled = progress_mod.wanted(no_progress=args.no_progress)
    with progress_mod.bar(plan.runs, "runs", enabled) as bar:

        def report(run: Run) -> None:
            mark = "ok " if run.state == VALID else "!! "
            detail = "" if run.state == VALID else f"  {run.state}: {run.detail}"
            bar.line(
                f"  {mark}{run.cell:<24} {run.duration}s  "
                f"{run.usage.get('input', 0)} in / {run.usage.get('output', 0)} out  "
                f"{run.usage.get('turns', 0)} turns  "
                f"{run.usage.get('retries', 0)} retries{detail}"
            )
            bar.tick()

        runner_mod.execute(plan, on_run=report)

    return _write_synthesis(plan.output, scenario)


def _blindness_lines(plan) -> list[str]:
    from .validation import describe_blindness

    return describe_blindness(plan.blindness, len(plan.scenario.cells))


# --- validate ----------------------------------------------------------------


def cmd_validate(args) -> int:
    """Everything `run` would refuse, checked before an output directory exists.

    The natural first command against a new scenario, and cheap enough for a CI
    hook: the same refusals `resolve` applies - the config entry for the
    repository, every referenced path, the thinking precondition - shared with it
    rather than reimplemented, so `validate` cannot pass what `run` would refuse.
    """
    scenario, config = _load(args)
    repo_path, repo_source = runner_mod.settle_repo(scenario, config)
    runner_mod.refuse_unmeasurable(scenario)

    print(f"{scenario.title or scenario.name}")
    print(f"  {len(scenario.cells)} cells x {scenario.protocol['repetitions']} repetitions")
    print(f"  etalon {scenario.task['etalon']} of {repo_source}")
    if repo_source != str(repo_path):
        print(f"  to be pinned at {repo_path} (cloned on the first run)")
    for v in scenario.validators:
        print(f"  {v.mode} validator, owning: {', '.join(v.metrics)}")
    for line in validation_mod.describe_blindness(
        validation_mod.blindness(scenario), len(scenario.cells)
    ):
        print(line)
    if not agent_mod.available():
        print(f"  ! {agent_mod.PI!r} is not on PATH: this validation holds, a run would refuse")
    print("ok: nothing this scenario references is missing")
    return 0


# --- render ----------------------------------------------------------------


def cmd_render(args) -> int:
    scenario = load_scenario(args.scenario)
    output = Output(args.output, scenario, args.repetitions)
    runs = output.read_measures()
    if not runs:
        print(f"error: no measures in {output.directory}", file=sys.stderr)
        return 1

    # Before the table, and independent of it. `_write_synthesis` returns without
    # writing anything when the matrix is incomplete, and an incomplete matrix is
    # exactly when somebody wants to read the traces.
    if args.html:
        code = _export_sessions(output, runs, no_progress=args.no_progress)
        if code:
            return code

    suffix = ""
    if args.reference:
        # A reference is a rendering choice, not a measurement. Another one is a
        # suffixed file from the same measures, never a hand-renamed matrix.
        scenario.verdict["reference"] = args.reference
        suffix = "_ref-" + args.reference.replace(" / ", "-").replace(" ", "-")
    return _write_synthesis(output, scenario, runs, suffix)


def _export_sessions(output: Output, runs: list[Run], no_progress: bool = False) -> int:
    """Rebuilds one HTML page per archived session, in the run's own directory.

    Costs no tokens: it reads jsonl already on disk. A session that will not render is
    reported and skipped, on the rule that holds everywhere else here - one broken run
    must not cost the rest.

    Runs with nothing archived are counted and named as such. An output tree measured
    before sessions were archived would otherwise produce a silence that reads as
    success.
    """
    if not agent_mod.available():
        print(
            f"error: {agent_mod.PI!r} is not on PATH, so no session can be exported",
            file=sys.stderr,
        )
        return 1

    written = bare = 0
    # Counted per run, not per session file: a run may archive several, and the
    # total has to be known before the first one is opened.
    enabled = progress_mod.wanted(no_progress=no_progress)
    with progress_mod.bar(len(runs), "sessions", enabled) as bar:
        for run in sorted(runs, key=lambda r: r.id):
            sessions = output.sessions(run.id)
            if not sessions:
                bare += 1
                bar.tick()
                continue
            for session in sessions:
                try:
                    path = agent_mod.export_html(session, session.parent)
                except RuntimeError as e:
                    bar.warn(f"  !! {run.id}/{session.name}: {e}")
                    continue
                written += 1
                bar.line(f"  {path.relative_to(output.directory)}")
            bar.tick()

    print(f"\n  {written} session page{'' if written == 1 else 's'} written")
    if bare:
        print(
            f"  {bare} of {len(runs)} runs without an archived session: measured before "
            f"sessions were archived, or the agent never started"
        )
    return 0


def _write_synthesis(
    output: Output, scenario, runs: list[Run] | None = None, suffix: str = ""
) -> int:
    runs = runs if runs is not None else output.read_measures()
    state = output.read_state()
    counts = output.summarise(state) if state else {}
    note = incomplete_note(counts) if state else ""

    if note:
        print(f"\n  {note}")
        return 0

    by_cell: dict[str, list[Run]] = {}
    for r in runs:
        by_cell.setdefault(r.cell, []).append(r)

    criterion = scenario.verdict["criterion"]
    validity = tuple(scenario.verdict.get("validity", ()))
    sample = next((r for r in runs if criterion in r.metrics), None)
    measures = table_mod.cost_measures() + (table_mod.criterion_measure(criterion, sample),)

    try:
        rows = table_mod.gap_rows(by_cell, scenario.reference, measures, validity)
    except ValueError as e:
        # The measures are safe on disk; only the rendering failed. Say so, because
        # a traceback here reads as "the matrix is lost" when nothing is lost.
        print(f"\nerror: {e}", file=sys.stderr)
        print(
            f"\n  The measures are intact in {output.directory / 'measures.json'}.\n"
            f"  Fix [verdict].validity and rerun `render` - no remeasuring needed.",
            file=sys.stderr,
        )
        return 1

    order = tuple(c.name for c in scenario.cells)
    draws = scenario.verdict.get("draws", 10_000)
    seed = scenario.verdict.get("seed", 20260729)

    tests, other = table_mod.scored_metrics(runs, scenario.declared_metrics)
    scores = table_mod.score_table(table_mod.score_rows(by_cell, tests, order), tests, other)
    spend = table_mod.spend_measures()
    cost = table_mod.spend_table(
        table_mod.spend_rows(by_cell, spend, validity, order, draws, seed), spend, draws, seed
    )
    gaps = table_mod.gap_table(rows, scenario.reference, draws, seed)
    text = "\n\n".join([scores, cost, gaps])
    header = [
        f"# {scenario.title or scenario.name}",
        "",
        f"- etalon `{scenario.task['etalon']}`, provider `{scenario.agent['provider']}`, "
        f"model `{scenario.agent['model']}`, thinking `{scenario.agent['thinking']}`",
        f"- {output.repetitions} repetitions, concurrency "
        f"{state.get('concurrency')}, timeout {state.get('timeout')}s",
    ]
    if state.get("overrides"):
        header.append(f"- overrides: {json.dumps(state['overrides'])}")
    header.append("")
    warning = table_mod.retry_warning(by_cell)
    path = output.write_synthesis("\n".join([*header, text, warning, ""]), suffix)
    print(f"\n  written {path}")
    return 0


# --- replay ----------------------------------------------------------------


def cmd_replay(args) -> int:
    """Re-scores archived runs by reconstituting their trees. Costs no tokens."""
    scenario, config = _load(args)
    base = Path(args.scenario).resolve().parent
    # Pinned like a run would: a replay costs no tokens, but it has always cost a clone,
    # and a scenario naming a URL has nothing to reconstitute from until it is pinned.
    source = runner_mod.prepare_source(config, scenario.task["repo"], scenario.task["etalon"])

    directory = args.directory
    runs = (
        [directory]
        if (directory / "diff.patch").is_file()
        else sorted(d for d in (directory / "runs").iterdir() if d.is_dir())
        if (directory / "runs").is_dir()
        else []
    )
    if not runs:
        print(f"error: no archived run found under {directory}", file=sys.stderr)
        return 1

    scored = None
    if args.rescore:
        scored = rescored_measures(directory, scenario)
        if scored is None:
            return 1

    print(f"replaying {len(runs)} runs from {directory}")
    at_etalon = repo_mod.etalon_files(source, scenario.task["etalon"])

    enabled = progress_mod.wanted(no_progress=args.no_progress)
    with progress_mod.bar(len(runs), "replayed", enabled) as bar:
        for run_dir in runs:
            patch = (
                (run_dir / "diff.patch").read_text() if (run_dir / "diff.patch").is_file() else ""
            )
            work = config.workdir() / "replay" / run_dir.name
            # Into `work/repo`, which is the run's own layout rather than tidiness. The
            # context is written beside the tree, in `clone.parent`, so cloning *into* `work`
            # made that `replay/` - one path shared by every run, where sixty contexts
            # overwrote each other and only the last survived. A distinct `context:` line was
            # printed for each all the same, so nothing looked wrong.
            #
            # No score was ever wrong: a context is written and consumed in the same turn of
            # this loop. What was lost is the artifact - "point your validators at those
            # contexts" was executable for one run out of sixty, and a validator failure could
            # not be reproduced by hand. And it is a race waiting for the day this loop runs
            # concurrently, which is when it would start producing plausible wrong numbers.
            clone = repo_mod.clone(source, scenario.task["etalon"], work / "repo")
            repo_mod.apply_diff(clone, patch)
            context = replay_context(run_dir, clone, source, scenario, at_etalon)
            bar.line(f"  {run_dir.name}: reconstituted at {clone}")
            bar.line(f"    context: {context}")
            if scored is not None:
                bar.line(f"    {rescore_run(scored, scenario, run_dir, context, base)}")
            bar.tick()

    if scored is not None:
        return publish_rescore(scored, scenario)

    print("\n  trees reconstituted, each with a context beside it. Point the scenario's")
    print("  validators at those contexts - re-scoring costs no tokens.")
    if UNREPLAYABLE:
        print(
            f"  a validator reading {', '.join(UNREPLAYABLE)} will refuse by name: "
            f"those lived in the work directory, which is disposable by design"
        )
    return 0


# --- replay --rescore ------------------------------------------------------
#
# What `replay` was missing to keep its own promise. It reconstituted trees and said
# "point the validators at those contexts", and there the trail ended: nothing wrote a
# score back, because only `run` and `form` ever wrote `measures.json`. So a corrected
# signature could be *executed* against sixty archived runs and the sixty answers had
# nowhere to go but a reader's own script - which is a second scoring path, of the kind
# this project refuses everywhere else.

REPETITIONS = re.compile(r"_n(\d+)$")


class Rescore:
    """The measures being rewritten, and what happened to each run.

    Holds the list in its **archived order** rather than rebuilding it: two identical
    scorings must produce a byte-identical `measures.json`, so a reordering would show
    up in `git diff` as a change that means nothing.
    """

    def __init__(self, output: Output, runs: list[Run]):
        self.output = output
        self.runs = runs
        self.by_id = {r.id: r for r in runs}
        self.rescored: list[str] = []
        self.skipped: list[str] = []


def rescored_measures(directory: Path, scenario) -> Rescore | None:
    """The measures `directory` holds, or None with the reason said out loud.

    A directory name **is** the experiment's identity (`outputs.experiment_name`), so it
    is also the check: re-scoring one matrix with another scenario's validators would
    rewrite measures that were never that matrix's, and comparing the two names catches
    it before anything is written.
    """
    found = REPETITIONS.search(directory.name)
    output = Output(directory.parent, scenario, int(found.group(1)) if found else None)
    if output.directory.resolve() != directory.resolve():
        print(
            f"refused: this scenario names {output.directory.name}, and you asked to "
            f"re-score {directory.name}. A directory name is the experiment's identity, "
            f"so re-scoring across the two would rewrite measures that are not its own",
            file=sys.stderr,
        )
        return None

    runs = output.read_measures()
    if not runs:
        print(
            f"error: no {'measures.json'} in {output.directory}, so there is nothing to "
            f"re-score. `replay` without --rescore still reconstitutes the trees",
            file=sys.stderr,
        )
        return None
    return Rescore(output, runs)


def rescore_run(scored: Rescore, scenario, run_dir: Path, context: Path, base: Path) -> str:
    """Re-scores one archived run against its fresh context, and says what happened.

    A **judge is not re-run.** Its verdict costs tokens, and `replay` exists on the
    promise that it costs none. The archived payload is reused instead, which is the right
    answer rather than a compromise: that verdict is a measurement somebody paid for, and
    correcting a script metric must not silently discard it. An archive without it refuses
    the run rather than scoring it short.
    """
    run = scored.by_id.get(run_dir.name)
    if run is None:
        scored.skipped.append(run_dir.name)
        return "not in measures.json, left alone"
    # `empty` means the run produced nothing - never launched, or launched and billed
    # nothing. No scoring can turn that into a measurement, and overwriting its state
    # would hide an incomplete matrix behind a full-looking one.
    if run.state == EMPTY:
        scored.skipped.append(run.id)
        return "produced nothing, so nothing to score"

    results = []
    for validator in scenario.validators:
        if validator.mode == "script":
            result = validation_mod.run_script(
                validator, context, scenario.protocol["timeout"], cwd=base
            )
            payload, stderr = result.payload, result.stderr
        else:
            archived = run_dir / "validation" / f"{validator.mode}.json"
            if not archived.is_file():
                scored.skipped.append(run.id)
                return f"no archived {validator.mode} verdict to reuse, left alone"
            payload, stderr = json.loads(archived.read_text()), ""
        # The run's score is being replaced, so the archived payload is replaced with it.
        # Leaving the old one would put a `measures.json` and a `validation/script.json`
        # side by side that disagree, and a reader has no way to tell which is the score.
        scored.output.write_validation(run.id, validator.mode, payload, stderr)
        results.append((validator.mode, payload))

    metrics, reasons, state, detail = measure_mod.merge(results, scenario.declared_metrics)
    was = run.state
    run.metrics, run.reasons = metrics, reasons
    # Set unconditionally, in both directions. A run whose validator used to fail becomes
    # valid when the fix works - which is the whole point - and a run that used to score
    # must be allowed to stop scoring, or a broken validator would read as the old result.
    run.state, run.detail = state, detail
    scored.rescored.append(run.id)

    if state != VALID:
        return f"{state}: {detail}"
    return "re-scored" if was == VALID else f"re-scored, was {was}"


def publish_rescore(scored: Rescore, scenario) -> int:
    """Writes the measures and the ledger back, then rebuilds the synthesis.

    `usage`, `duration` and `attempts` are never touched: they are facts about the run,
    not about the scoring, and a re-score that rewrote them would claim to have measured
    something it did not. That is also why `Output.record` is not reused here - it adds to
    `attempts`, and "attempts are counted per run so an abusive resume leaves a trace" is
    an invariant a re-scoring must not spend.

    `state.json` **must** move with the measures, though, and it is the one thing that
    could not simply be left alone. It carries the per-run state that decides whether a
    synthesis is publishable at all, so a validator repaired here would still be counted
    among the failures and the matrix would keep refusing to publish - the exact case this
    flag exists for.
    """
    path = scored.output.write_measures(scored.runs)
    print(f"\n  {len(scored.rescored)} of {len(scored.runs)} runs re-scored, no tokens spent")
    print(f"  written {path}")
    if scored.skipped:
        print(f"  left alone: {', '.join(sorted(scored.skipped))}")

    state = scored.output.read_state()
    if state.get("runs"):
        for run_id_ in scored.rescored:
            entry = state["runs"].get(run_id_)
            run = scored.by_id[run_id_]
            if entry is None:
                continue
            entry["state"] = run.state
            # Removed and not left behind when the run now scores: a stale detail would
            # explain a failure that no longer exists.
            if run.detail:
                entry["detail"] = run.detail
            else:
                entry.pop("detail", None)
        scored.output.write_state(state)

    return _write_synthesis(scored.output, scenario, scored.runs)


# What a replay cannot put back. The prompt and the agent's final prose lived in the work
# directory, which the OS may purge; the raw stream is deliberately never archived
# (`outputs.py:24-27`). A validator reading one of them refuses **by name**, thanks to
# `Assay._given` - which is why no context version number is needed: "the context carries
# no 'response'" tells a reader more than "this archive is version 1" ever could.
UNREPLAYABLE = ("prompt", "response", "trace")


def replay_context(run_dir: Path, clone: Path, source: Path, scenario, at_etalon: list) -> Path:
    """Writes the context a re-scoring needs, beside the tree just reconstituted.

    Until now `replay` rebuilt a tree and said "run the validators against them", while
    the only context on disk was the **archived** one - full of absolute paths into a work
    directory under `$TMPDIR` that the system may long have purged. So the archived
    context named a tree that no longer existed, and the fresh tree was named by nothing.

    "Fix a signature and re-score runs already paid for" is the promise that justifies
    archiving a tag and a diff instead of a hundred and fifty working trees, and it was
    not executable.

    What goes in is what the archive actually holds: `touched` recomputed from the
    reconstituted tree, `files` from the tag, and the **archived session** - which is what
    makes a metric of process replayable at all, its tool calls being in the session and
    not only in the stream that is thrown away.
    """
    configuration = run_dir / "configuration.json"
    cell = ""
    if configuration.is_file():
        cell = json.loads(configuration.read_text()).get("cell", "")

    return validation_mod.write_context(
        clone.parent / "validation",
        repo=clone,
        etalon=scenario.task["etalon"],
        etalon_checkout=source,
        prompt_file=None,
        session_dir=run_dir / SESSION,
        trace=None,
        cell=cell,
        repetition=0,
        test_command=scenario.task.get("test_command"),
        prepare=list(scenario.task.get("prepare", ())),
        touched=repo_mod.changed_files(clone),
        files=at_etalon,
        declared=scenario.declared_metrics,
    )


# --- compare ---------------------------------------------------------------


def cmd_compare(args) -> int:
    left, right = _read_experiment(args.left), _read_experiment(args.right)
    if left is None or right is None:
        return 1

    # Hard refusals first. A different etalon is a different baseline, so the two
    # measures are not of the same thing.
    if left["etalon"] != right["etalon"]:
        print(
            f"refused: different etalons, {left['etalon']} against {right['etalon']}",
            file=sys.stderr,
        )
        return 1

    print(f"comparing {args.left.name} against {args.right.name}")
    differing = [
        k
        for k in ("provider", "model", "thinking", "repetitions", "concurrency", "timeout")
        if left.get(k) != right.get(k)
    ]
    same = [
        k for k in ("etalon", "provider", "model", "repetitions") if left.get(k) == right.get(k)
    ]
    print(
        f"  declared differences: {', '.join(f'{k} ({left.get(k)} / {right.get(k)})' for k in differing) or 'none'}"
    )
    print(f"  identical: {', '.join(same)}")

    # Cost columns are contaminated by retries, which reflect our own load on the
    # provider rather than the configuration.
    lr, rr = _retries(left), _retries(right)
    if lr or rr:
        print(
            f"  ! cost columns set aside: retries {lr} on the left, {rr} on the right\n"
            f"    -> tokens and durations would reflect our own load"
        )

    left_runs, right_runs = _measured(args.left), _measured(args.right)
    if not left_runs or not right_runs:
        sides = [d.name for d, r in ((args.left, left_runs), (args.right, right_runs)) if not r]
        print(f"  no measures.json in {', '.join(sides)}: nothing to tabulate")
        return 0

    by_left: dict[str, list[Run]] = {}
    for r in left_runs:
        by_left.setdefault(r.cell, []).append(r)
    by_right: dict[str, list[Run]] = {}
    for r in right_runs:
        by_right.setdefault(r.cell, []).append(r)

    # The metric contract lives in the scenario, which `compare` deliberately does
    # not take: two experiments may come from two scenarios. What both sides
    # actually measured is in the rows themselves.
    everything = [*left_runs, *right_runs]
    declared = tuple(dict.fromkeys(k for r in everything for k in r.metrics))
    tests, _ = table_mod.scored_metrics(everything, declared)
    print()
    print(
        table_mod.compare_table(
            table_mod.compare_rows(by_left, by_right, tests), tests, args.left.name, args.right.name
        )
    )
    return 0


def _read_experiment(directory: Path) -> dict | None:
    state = directory / "state.json"
    if not state.is_file():
        print(f"error: no state.json in {directory}", file=sys.stderr)
        return None
    data = json.loads(state.read_text())
    data["_dir"] = directory
    return data


def _measured(directory: Path) -> list[Run]:
    """The archived measures, or nothing - which the caller says out loud."""
    path = directory / "measures.json"
    if not path.is_file():
        return []
    return [Run(**row) for row in json.loads(path.read_text())]


def _retries(experiment: dict) -> int:
    measures = experiment["_dir"] / "measures.json"
    if not measures.is_file():
        return 0
    return sum(
        (row.get("usage") or {}).get("retries") or 0 for row in json.loads(measures.read_text())
    )


# --- parity ----------------------------------------------------------------


def cmd_parity(args) -> int:
    """Layer 3 always; layer 1 with an archive; layer 4 with --smoke."""
    if args.smoke:
        workdir = args.workdir
        if workdir is None:
            workdir = config_mod.load(args.config).workdir()
        print(f"layer 4 - smoke pass over {args.smoke.name}")
        problems = parity_mod.layer4(args.smoke, workdir)
        failures = [p for p in problems if not p.endswith("declared thinking level")]
        for line in problems:
            print(f"  {line}")
        if not failures:
            print("\n  every mechanical criterion holds. No statistical claim: a smoke")
            print("  pass at small n concludes nothing about any configuration.")
        return 0 if not failures else 1

    if not args.measures:
        print("error: give a measures file, or --smoke <experiment dir>", file=sys.stderr)
        return 2

    print("layer 3 - aggregation and verdict, from the published per-run rows")
    rows = parity_mod.layer3(args.measures, args.reference, args.criterion)
    for row in rows:
        cells = "  ".join(
            f"{c['measure']}={c['rendered']} {'*' if c['state'] == 'established' else 'o'}"
            for c in row["measures"]
        )
        print(f"  {row['cell']:<26} {cells}")

    if not args.archive:
        print("\n  layers 1 and 2 need --archive (the bench's run directories)")
        return 0

    print("\nlayer 1 - stripping, from the archived sessions")
    problems = parity_mod.layer1(args.measures, args.archive)
    for line in problems or ["  every session reproduces its recorded tokens and turns"]:
        print(f"  {line}")
    return 0 if not problems else 1


# --- form ------------------------------------------------------------------


def cmd_form(args) -> int:
    scenario = load_scenario(args.scenario)
    output = Output(args.output, scenario)
    runs = output.read_measures()
    if not runs:
        print(f"error: no measures in {output.directory}", file=sys.stderr)
        return 1

    if args.ingest:
        return _ingest_form(output, runs, args.ingest)

    manual = [m for v in scenario.validators if v.mode == "form" for m in v.metrics]
    if not manual:
        print("this scenario declares no form validator")
        return 0

    # Shuffled and with cell names withheld, the same blinding as the judge and for
    # the same reason: someone who knows they are scoring the best equipped cell
    # scores it better. The id-to-cell mapping lives in state.json, deliberately
    # not here.
    import random

    order = sorted(runs, key=lambda r: r.id)
    random.Random(scenario.verdict.get("seed", 20260729)).shuffle(order)

    lines = [
        f"# Manual scoring for {scenario.name}",
        "",
        "Shuffled, and cell names are withheld on purpose: do not try to work out",
        "which configuration you are scoring. An absent key means not yet filled,",
        "so this file parses at any point.",
        "",
    ]
    for r in order:
        lines.append(f"[run.{r.id}]")
        lines.append(f'diff = "runs/{r.id}/diff.patch"')
        for metric in manual:
            lines.append(f"# {metric} =")
        lines.append("")

    path = output.directory / f"form-{scenario.name}.toml"
    path.write_text("\n".join(lines))
    print(f"written {path}  ({len(order)} runs, metrics: {', '.join(manual)})")
    return 0


def _ingest_form(output: Output, runs: list[Run], form: Path) -> int:
    import tomllib

    from .measure import fill_manual

    filled = tomllib.loads(form.read_text()).get("run", {})
    by_id = {r.id: r for r in runs}
    merged = refused = pending = 0
    for run_id, values in filled.items():
        run = by_id.get(run_id)
        if run is None:
            continue
        manual = {k: v for k, v in values.items() if k != "diff"}
        if not manual:
            pending += 1
            continue
        rejected = fill_manual(run, manual)
        refused += len(rejected)
        merged += len(manual) - len(rejected)
    output.write_measures(list(by_id.values()))
    print(f"{merged} manual metrics merged, {pending} still pending, {refused} refused")
    if refused:
        print(
            "  refused values would have overwritten a measured metric, which a form may never do"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
