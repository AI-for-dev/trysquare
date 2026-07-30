"""The command line.

Six subcommands, and `--output` roots every one of them that writes.

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
import sys
from pathlib import Path

from . import agent as agent_mod
from . import config as config_mod
from . import parity as parity_mod
from . import repo as repo_mod
from . import runner as runner_mod
from . import table as table_mod
from .measure import Run, VALID
from .outputs import Output, incomplete_note
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

    run = with_common(sub.add_parser("run", help="measure a scenario"))
    run.add_argument("--repetitions", type=int, help="override, stamped into the directory name")
    run.add_argument("--concurrency", type=int, help="override, recorded in the state")
    run.add_argument("--timeout", type=int, help="override, recorded in the state")
    run.add_argument("--only", action="append", default=[], help="restrict to these cells (repeatable)")
    run.add_argument("--resume", action="store_true", help="fill only what produced nothing")
    run.add_argument("--dry-run", action="store_true", help="show the plan and spend nothing")
    run.set_defaults(func=cmd_run)

    render = with_common(sub.add_parser("render", help="rebuild tables from stored measures"))
    render.add_argument("--repetitions", type=int, help="which matrix to read")
    render.add_argument("--reference", help="score against another cell, without remeasuring")
    render.add_argument(
        "--html",
        action="store_true",
        help="also export each archived session to HTML, in its own run directory",
    )
    render.set_defaults(func=cmd_render)

    replay = sub.add_parser("replay", help="re-score archived runs without spending tokens")
    replay.add_argument("directory", type=Path, help="an experiment directory, or one run inside it")
    replay.add_argument("--config", type=Path)
    replay.add_argument("--scenario", type=Path, required=True)
    replay.set_defaults(func=cmd_replay)

    compare = sub.add_parser("compare", help="compare two experiments")
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)
    compare.set_defaults(func=cmd_compare)

    parity = sub.add_parser("parity", help="check this harness against the previous bench")
    parity.add_argument("measures", nargs="?", type=Path, help="the bench's published measures JSON")
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


def collect_overrides(args) -> dict:
    out = {}
    for key in (*STAMPED, *RECORDED):
        value = getattr(args, key, None)
        if value is not None:
            out[key] = value
    return out


def cmd_run(args) -> int:
    scenario = load_scenario(args.scenario)
    config = config_mod.load(args.config, start=Path(args.scenario).resolve().parent)
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

    def report(run: Run) -> None:
        mark = "ok " if run.state == VALID else "!! "
        detail = "" if run.state == VALID else f"  {run.state}: {run.detail}"
        print(
            f"  {mark}{run.cell:<24} {run.duration}s  "
            f"{run.usage.get('input', 0)} in / {run.usage.get('output', 0)} out  "
            f"{run.usage.get('turns', 0)} turns  "
            f"{run.usage.get('retries', 0)} retries{detail}",
            flush=True,
        )

    runner_mod.execute(plan, on_run=report)
    return _write_synthesis(plan.output, scenario)


def _blindness_lines(plan) -> list[str]:
    from .validation import describe_blindness

    return describe_blindness(plan.blindness, len(plan.scenario.cells))


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
        code = _export_sessions(output, runs)
        if code:
            return code

    suffix = ""
    if args.reference:
        # A reference is a rendering choice, not a measurement. Another one is a
        # suffixed file from the same measures, never a hand-renamed matrix.
        scenario.verdict["reference"] = args.reference
        suffix = "_ref-" + args.reference.replace(" / ", "-").replace(" ", "-")
    return _write_synthesis(output, scenario, runs, suffix)


def _export_sessions(output: Output, runs: list[Run]) -> int:
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
    for run in sorted(runs, key=lambda r: r.id):
        sessions = output.sessions(run.id)
        if not sessions:
            bare += 1
            continue
        for session in sessions:
            try:
                path = agent_mod.export_html(session, session.parent)
            except RuntimeError as e:
                print(f"  !! {run.id}/{session.name}: {e}", file=sys.stderr)
                continue
            written += 1
            print(f"  {path.relative_to(output.directory)}")

    print(f"\n  {written} session page{'' if written == 1 else 's'} written")
    if bare:
        print(
            f"  {bare} of {len(runs)} runs without an archived session: measured before "
            f"sessions were archived, or the agent never started"
        )
    return 0


def _write_synthesis(output: Output, scenario, runs: list[Run] | None = None, suffix: str = "") -> int:
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

    text = table_mod.gap_table(
        rows,
        scenario.reference,
        scenario.verdict.get("draws", 10_000),
        scenario.verdict.get("seed", 20260729),
    )
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
    scenario = load_scenario(args.scenario)
    config = config_mod.load(args.config, start=Path(args.scenario).resolve().parent)
    # Pinned like a run would: a replay costs no tokens, but it has always cost a clone,
    # and a scenario naming a URL has nothing to reconstitute from until it is pinned.
    source = runner_mod.prepare_source(config, scenario.task["repo"], scenario.task["etalon"])

    directory = args.directory
    runs = [directory] if (directory / "diff.patch").is_file() else sorted(
        d for d in (directory / "runs").iterdir() if d.is_dir()
    ) if (directory / "runs").is_dir() else []
    if not runs:
        print(f"error: no archived run found under {directory}", file=sys.stderr)
        return 1

    print(f"replaying {len(runs)} runs from {directory}")
    for run_dir in runs:
        patch = (run_dir / "diff.patch").read_text() if (run_dir / "diff.patch").is_file() else ""
        work = config.workdir() / "replay" / run_dir.name
        clone = repo_mod.clone(source, scenario.task["etalon"], work)
        repo_mod.apply_diff(clone, patch)
        print(f"  {run_dir.name}: reconstituted at {clone}")
    print("\n  trees reconstituted; run the scenario's validators against them")
    return 0


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
        k for k in ("provider", "model", "thinking", "repetitions", "concurrency", "timeout")
        if left.get(k) != right.get(k)
    ]
    same = [
        k for k in ("etalon", "provider", "model", "repetitions")
        if left.get(k) == right.get(k)
    ]
    print(f"  declared differences: {', '.join(f'{k} ({left.get(k)} / {right.get(k)})' for k in differing) or 'none'}")
    print(f"  identical: {', '.join(same)}")

    # Cost columns are contaminated by retries, which reflect our own load on the
    # provider rather than the configuration.
    lr, rr = _retries(left), _retries(right)
    if lr or rr:
        print(
            f"  ! cost columns set aside: retries {lr} on the left, {rr} on the right\n"
            f"    -> tokens and durations would reflect our own load"
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


def _retries(experiment: dict) -> int:
    measures = experiment["_dir"] / "measures.json"
    if not measures.is_file():
        return 0
    return sum((row.get("usage") or {}).get("retries") or 0 for row in json.loads(measures.read_text()))


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
        print("  refused values would have overwritten a measured metric, which a form may never do")
    return 0


if __name__ == "__main__":
    sys.exit(main())
