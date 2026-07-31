"""The exact layers of parity: scoring and aggregation, checked without a token.

The fixture is the bench's own published measures for the 2x3 matrix of module
2.1 - 60 per-run rows, 6 cells of 10. The expected values below are what the
bench *published*, copied from its markdown output.

This is the strongest test in the suite and the cheapest: no tokens, no model, no
sampling noise. Identical inputs through an identical method must give identical
output. If this fails, exactly one of three things is true, and the third is the
one worth remembering: this harness is wrong, or the bench was wrong and a
published number needs correcting, or the archive is missing something.
"""

import json
from pathlib import Path

from trysquare.parity import (
    archived_runs,
    compare,
    layer2,
    layer3,
    published_by_id,
    read_bench_measures,
)

FIXTURE = Path(__file__).parent / "fixtures" / "bench_2x3_n10.json"

# Copied from the bench's published table, reference cell `base`:
#
#   | +AGENTS.md               | +11 502 o | -284 o  | +1 o | -8 o   | -10 pts o  |
#   | +AGENTS.md +thinking     | +43 248 * | +4 767 *| +2 o | +144 * | -80 pts *  |
#   | +thinking                | +72 289 * | +6 175 *| +4 o | +213 * | -30 pts o  |
#   | +ticket soigne           | +15 929 o | -296 o  | +3 * | +7 o   | -100 pts * |
#   | +ticket soigne +thinking | +52 979 * | +6 873 *| +4 * | +224 * | -100 pts * |
PUBLISHED = {
    "+AGENTS.md": {
        "in": "+11 502 o",
        "out": "-284 o",
        "turns": "+1 o",
        "duration": "-8 o",
        "overflow": "-10 pts o",
    },
    "+AGENTS.md +thinking": {
        "in": "+43 248 *",
        "out": "+4 767 *",
        "turns": "+2 o",
        "duration": "+144 *",
        "overflow": "-80 pts *",
    },
    "+thinking": {
        "in": "+72 289 *",
        "out": "+6 175 *",
        "turns": "+4 o",
        "duration": "+213 *",
        "overflow": "-30 pts o",
    },
    "+ticket soigné": {
        "in": "+15 929 o",
        "out": "-296 o",
        "turns": "+3 *",
        "duration": "+7 o",
        "overflow": "-100 pts *",
    },
    "+ticket soigné +thinking": {
        "in": "+52 979 *",
        "out": "+6 873 *",
        "turns": "+4 *",
        "duration": "+224 *",
        "overflow": "-100 pts *",
    },
}


class TestArchive:
    """What the archive contains is a fact this suite pins down."""

    def test_sixty_runs_in_six_cells(self):
        by_cell = read_bench_measures(FIXTURE)
        assert len(by_cell) == 6
        assert sum(len(v) for v in by_cell.values()) == 60
        for cell, runs in by_cell.items():
            assert len(runs) == 10, f"{cell} should have 10 runs"

    def test_per_run_rows_carry_their_scoring(self):
        """Exact parity is only possible because scoring is archived per run."""
        run = read_bench_measures(FIXTURE)["base"][0]
        for metric in ("overflow", "delivered", "tests", "api_stable", "in_scope"):
            assert metric in run.metrics
        assert run.usage["input"] > 0
        assert run.usage["turns"] > 0

    def test_the_matrix_had_no_retries(self):
        """Which is what lets the cost columns be read at all."""
        by_cell = read_bench_measures(FIXTURE)
        total = sum(r.retries for runs in by_cell.values() for r in runs)
        assert total == 0


class TestLayer3:
    def test_reproduces_the_published_gap_table(self):
        rows = layer3(FIXTURE, reference="base", criterion="overflow")
        problems = compare(rows, PUBLISHED)
        assert problems == [], "\n".join(["parity broken:", *problems])

    def test_the_headline_result_survives(self):
        """The result module 2.1 rests on, asserted on its own.

        A well written ticket separates completely: -100 points, established.
        """
        rows = layer3(FIXTURE, reference="base", criterion="overflow")
        row = next(r for r in rows if r["cell"] == "+ticket soigné")
        overflow = next(c for c in row["measures"] if c["measure"] == "overflow")
        assert overflow["rendered"] == "-100 pts"
        assert overflow["state"] == "established"

    def test_the_rule_alone_stays_inconclusive(self):
        """The other half of the lesson, and it is a negative result.

        A convention filed in AGENTS.md, without paying for the reasoning that
        goes and reads it, does not establish anything at n=10.
        """
        rows = layer3(FIXTURE, reference="base", criterion="overflow")
        row = next(r for r in rows if r["cell"] == "+AGENTS.md")
        overflow = next(c for c in row["measures"] if c["measure"] == "overflow")
        assert overflow["state"] == "inconclusive"


class TestLayer2:
    """Scoring, with the reconstitution injected so the layer itself stays offline.

    What the wiring does with a real repository and a real validator is checked end to
    end in `tests/test_cli.py::TestParityLayer2`.
    """

    def archive(self, tmp_path, notes: dict) -> tuple[Path, Path]:
        """A mini bench archive: one row and one diff per run, French keys and all."""
        archive = tmp_path / "archive"
        rows = []
        for ident, note in notes.items():
            (archive / ident).mkdir(parents=True)
            (archive / ident / "diff.patch").write_text("")
            rows.append({"identifiant": ident, "cellule": "base", "note": note})
        measures = tmp_path / "m.json"
        measures.write_text(json.dumps(rows))
        return measures, archive

    def scored(self, tmp_path, notes: dict, metrics: dict):
        measures, archive = self.archive(tmp_path, notes)
        return layer2(measures, archive, lambda run_dir: run_dir, lambda context: metrics)

    def test_agreement_is_exact_and_counted(self, tmp_path):
        report = self.scored(
            tmp_path,
            {"base-0": {"debordement": 40, "livre": True}},
            {"overflow": 40, "delivered": True},
        )
        assert report.holds
        assert "1/1 runs re-score" in report.observed[0]

    def test_a_wrong_published_value_is_named_with_both_sides(self, tmp_path):
        """Named, not counted: a gap is only actionable if a reader can see which run,
        which metric, and what the two computations said."""
        report = self.scored(
            tmp_path,
            {"base-0": {"debordement": 40}},
            {"overflow": 90},
        )
        assert not report.holds
        assert "base-0/overflow: bench 40, here 90" in report.problems

    def test_a_metric_no_validator_returns_is_out_of_scope_not_a_gap(self, tmp_path):
        """The bench scored some metrics with a judge, whose verdict costs tokens and is
        not in this archive. Reporting those as disagreements would be sixty lies."""
        report = self.scored(
            tmp_path,
            {"base-0": {"debordement": 40, "apiStable": True}},
            {"overflow": 40},
        )
        assert report.holds
        assert any("api_stable" in line and "out of scope" in line for line in report.observed)

    def test_a_metric_missing_on_some_runs_only_blocks(self, tmp_path):
        """The opposite case: a validator that answers for one run and not the next is
        unreliable, and that is a defect rather than a scope statement."""
        measures, archive = self.archive(
            tmp_path, {"base-0": {"debordement": 40}, "base-1": {"debordement": 40}}
        )
        answers = iter(({"overflow": 40}, {}))
        report = layer2(measures, archive, lambda d: d, lambda c: next(answers))
        assert not report.holds
        assert any("missing on the rest" in p for p in report.problems)

    def test_a_reconstitution_that_fails_is_reported_and_the_layer_goes_on(self, tmp_path):
        measures, archive = self.archive(
            tmp_path, {"base-0": {"debordement": 40}, "base-1": {"debordement": 40}}
        )

        def reconstitute(run_dir: Path) -> Path:
            if run_dir.name == "base-0":
                raise OSError("the diff does not apply")
            return run_dir

        report = layer2(measures, archive, reconstitute, lambda c: {"overflow": 40})
        assert any("base-0: could not re-score" in p for p in report.problems)
        assert "1/1 runs re-score" in report.observed[0]

    def test_a_comparison_of_nothing_is_not_an_agreement(self, tmp_path):
        """Every published metric out of scope means the layer compared nothing, and
        "1/1 runs re-score exactly" would read as a reassurance it did not earn."""
        report = self.scored(tmp_path, {"base-0": {"apiStable": True}}, {"overflow": 40})
        assert not report.holds
        assert any("compared nothing" in p for p in report.problems)

    def test_a_run_without_a_diff_is_not_in_scope(self, tmp_path):
        measures, archive = self.archive(tmp_path, {"base-0": {"debordement": 40}})
        (archive / "base-0" / "diff.patch").unlink()
        assert archived_runs(published_by_id(measures), archive) == []
        report = layer2(measures, archive, lambda d: d, lambda c: {})
        assert any("no archived diff" in p for p in report.problems)
