"""The one module here that runs git, and the only one that can settle the question.

Every other test asserts which flags get built. This one builds a repository, pins it
through a `file://` URL and clones a run out of the pinned directory, because that is the
only way to find out whether a pinned source really carries the tag its runs ask for.
`git clone --help` documents that `--branch` accepts a tag and says nothing about what
`--no-tags` then keeps, so the question is settled here by doing it.

Offline throughout: a `file://` URL never reaches a network. Skipped rather than failed
when git is absent, which costs nothing because the tool requires git to measure anything
at all.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from trysquare import config as config_mod
from trysquare import repo, runner

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "validators"))

from signatures import game_sources  # noqa: E402

# The machine's own gitconfig must not decide whether this passes: a globally configured
# signing key, default branch name or absent user.email would each break it somewhere
# else than here.
ISOLATION = (
    "-c", "init.defaultBranch=main",
    "-c", "user.email=t@t",
    "-c", "user.name=t",
    "-c", "commit.gpgsign=false",
)


def git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *ISOLATION, *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


@unittest.skipUnless(shutil.which("git"), "git is not on PATH")
class TestPinningARemote(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.origin = self.home / "origin"
        (self.origin / "game").mkdir(parents=True)
        (self.origin / "game" / "neon.js").write_text("export const paddle = 1\n")
        git(["init", "-q"], cwd=self.origin)
        git(["add", "-A"], cwd=self.origin)
        git(["commit", "-qm", "the etalon"], cwd=self.origin)
        git(["tag", "etalon-v1"], cwd=self.origin)
        self.head = git(["rev-parse", "HEAD"], cwd=self.origin)

        self.url = f"file://{self.origin}"
        path = self.home / config_mod.CONFIG_NAME
        path.write_text(
            f'[repos]\nneon = "{self.url}"\n[defaults]\nworkdir = "{self.home / "work"}"\n'
        )
        self.config = config_mod.load(path)

    def pin(self, etalon: str = "etalon-v1") -> Path:
        return runner.prepare_source(self.config, "neon", etalon)

    def test_a_pinned_source_carries_the_tag_its_runs_clone_by(self):
        """The load-bearing property: runs clone from here with `--branch <tag>`.

        Git turns out to keep the tag named by `--branch` even under `--no-tags`, so this
        would likely hold either way - but nothing documents that, and this is the
        assertion that would notice if it stopped being true.
        """
        pinned = self.pin()
        self.assertEqual(git(["rev-parse", "etalon-v1^{commit}"], cwd=pinned), self.head)

    def test_a_run_can_be_cloned_out_of_the_pinned_source(self):
        target = repo.clone(self.pin(), "etalon-v1", self.home / "run" / "repo")
        self.assertTrue((target / "game" / "neon.js").is_file())
        self.assertEqual(git(["rev-parse", "HEAD"], cwd=target), self.head)

    def test_the_pinned_source_is_a_working_tree_a_validator_can_read(self):
        """The reason this is a clone and not a bare mirror.

        `etalon.checkout` is walked as files by `validators/neon.py`. A bare repository
        passes its `is_dir()` check and yields an empty reference, so every signature
        comparison in the matrix would report a plausible number about nothing.
        """
        self.assertIn("paddle", game_sources(self.pin()))

    def test_the_commit_behind_the_tag_is_recorded_for_the_archive(self):
        self.assertEqual(repo.commit_of(self.pin(), "etalon-v1"), self.head)
        self.assertIsNone(repo.commit_of(self.pin(), "no-such-tag"))

    def test_pinning_twice_clones_once(self):
        first = self.pin()
        stamp = (first / runner.READY).stat().st_mtime_ns
        self.assertEqual(self.pin(), first)
        self.assertEqual((first / runner.READY).stat().st_mtime_ns, stamp)

    def test_a_second_etalon_is_pinned_beside_the_first(self):
        """Keyed by tag, so a cache hit is by construction already at the right tag and
        there is no staleness to reason about."""
        git(["tag", "etalon-v2"], cwd=self.origin)
        self.assertNotEqual(self.pin("etalon-v2"), self.pin("etalon-v1"))
        self.assertEqual(git(["rev-parse", "etalon-v2^{commit}"], cwd=self.pin("etalon-v2")), self.head)

    def test_the_archive_records_the_url_and_the_commit_it_resolved_to(self):
        """Without these, a published archive cannot say what it measured.

        With a URL the address is the only thing identifying the repository, and the
        commit is the only trace left when a tag is moved between two matrices.
        """
        import json

        from trysquare.scenario import load

        root = Path(__file__).resolve().parent.parent
        scenario = load(root / "scenarios" / "2x3.toml")
        plan = runner.resolve(scenario, self.config, self.home / "out")
        pinned = self.pin()
        clone = repo.clone(pinned, "etalon-v1", self.home / "run" / "repo")

        runner.archive(
            plan,
            "r0",
            clone,
            repo.Prepared(path=clone, etalon="etalon-v1"),
            scenario.cells[0],
            "off",
        )
        written = json.loads(
            (plan.output.run_dir("r0") / "configuration.json").read_text()
        )
        self.assertEqual(written["repo"], self.url)
        self.assertEqual(written["etalon_commit"], self.head)

    def test_a_tag_that_does_not_exist_names_the_tag_and_the_url(self):
        with self.assertRaises(repo.RepoError) as e:
            self.pin("etalon-v9")
        message = str(e.exception)
        self.assertIn("etalon-v9", message)
        self.assertIn(self.url, message)
        self.assertIn("ls-remote", message)


if __name__ == "__main__":
    unittest.main()
