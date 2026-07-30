"""Archiving the agent's own traces, and rendering them back as HTML.

The traces were always jsonl, and they were always written where the OS is free to
delete them: under a work directory the documentation itself calls disposable. So the
only durable evidence of what an agent did was the diff it left behind, while the output
tree's own layout claimed to hold `runs/<id>/session/*.jsonl`.

Two things are guarded here. That the archive keeps *this* launch's sessions and not a
previous one's - the work directory is keyed by a stable run id, so the previous
measurement's files are still sitting there. And that `render --html` says what it did,
including when there was nothing to do.

Nothing here spends a token. The one test that runs the agent runs it on a fixture, in
`--export` mode, which reads a file and returns.
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from trysquare import agent, outputs
from trysquare.cli import main
from trysquare.measure import VALID, Run
from trysquare.scenario import parse

from tests.test_scenario import GRID

FIXTURE = Path(__file__).parent / "fixtures" / "session-minimal.jsonl"
ROOT = Path(__file__).resolve().parent.parent
SCENARIO = str(ROOT / "scenarios" / "2x3.toml")


def output() -> outputs.Output:
    return outputs.Output(Path(tempfile.mkdtemp()), parse(GRID))


def session_dir(*names: str) -> Path:
    """A work directory holding the named sessions, with the fixture as content."""
    directory = Path(tempfile.mkdtemp()) / "session"
    directory.mkdir()
    for name in names:
        (directory / name).write_bytes(FIXTURE.read_bytes())
    return directory


class TestArchiving(unittest.TestCase):
    def test_sessions_land_beside_the_run(self):
        o = output()
        copied = o.archive_sessions("abcd1234", session_dir("one.jsonl", "two.jsonl"))
        self.assertEqual([p.name for p in copied], ["one.jsonl", "two.jsonl"])
        self.assertEqual(
            copied[0].parent, o.directory / "runs" / "abcd1234" / outputs.SESSION
        )

    def test_the_content_is_copied_verbatim(self):
        """A trace that is not byte-for-byte the agent's own is not a trace."""
        o = output()
        copied = o.archive_sessions("abcd1234", session_dir("one.jsonl"))
        self.assertEqual(copied[0].read_bytes(), FIXTURE.read_bytes())

    def test_an_earlier_launch_is_not_imported(self):
        """The work directory keeps a run's sessions across launches: the run id is
        stable, so the path is. Copying whatever is there would mix a previous
        measurement's traces into an archive whose measures.json does not describe them.
        """
        o = output()
        copied = o.archive_sessions(
            "abcd1234",
            session_dir("old.jsonl", "new.jsonl"),
            exclude={"old.jsonl"},
        )
        self.assertEqual([p.name for p in copied], ["new.jsonl"])
        self.assertEqual([p.name for p in o.sessions("abcd1234")], ["new.jsonl"])

    def test_a_relaunch_replaces_the_archive_rather_than_adding_to_it(self):
        """Relaunching an experiment overwrites it, so the archive must overwrite too.

        Otherwise the previous launch's session is attributed to this one: the file count
        stops matching `attempts`, and two measurements sit under one name.
        """
        o = output()
        o.archive_sessions("abcd1234", session_dir("first.jsonl"))
        o.archive_sessions("abcd1234", session_dir("second.jsonl"))
        self.assertEqual([p.name for p in o.sessions("abcd1234")], ["second.jsonl"])

    def test_a_stale_page_does_not_survive_a_relaunch(self):
        """A page rendered from the previous trace would sit there looking current."""
        o = output()
        o.archive_sessions("abcd1234", session_dir("first.jsonl"))
        stale = o.sessions("abcd1234")[0].with_suffix(".html")
        stale.write_text("<!DOCTYPE html>")
        o.archive_sessions("abcd1234", session_dir("second.jsonl"))
        self.assertFalse(stale.exists())

    def test_a_launch_that_produced_nothing_clears_the_archive(self):
        """A run whose agent never started must not inherit the last trace."""
        o = output()
        o.archive_sessions("abcd1234", session_dir("first.jsonl"))
        o.archive_sessions("abcd1234", session_dir())
        self.assertEqual(o.sessions("abcd1234"), [])

    def test_an_absent_session_directory_is_not_an_error(self):
        """The agent may have failed to start. That is a run to record, not a crash."""
        o = output()
        self.assertEqual(o.archive_sessions("abcd1234", Path("/nonexistent/session")), [])

    def test_nothing_to_archive_creates_no_run_directory(self):
        """An empty `runs/<id>/` would read as a run that left something behind."""
        o = output()
        o.archive_sessions("abcd1234", session_dir())
        self.assertFalse((o.directory / "runs" / "abcd1234").exists())

    def test_sessions_of_a_run_that_has_none(self):
        self.assertEqual(output().sessions("abcd1234"), [])


@unittest.skipUnless(agent.available(), f"{agent.PI!r} is not on PATH")
class TestExport(unittest.TestCase):
    """The agent renders its own sessions, so nothing here reimplements its format."""

    def test_the_page_sits_beside_the_jsonl_under_the_same_stem(self):
        o = output()
        o.archive_sessions("abcd1234", session_dir("trace.jsonl"))
        session = o.sessions("abcd1234")[0]
        page = agent.export_html(session, session.parent)
        self.assertEqual(page, session.parent / "trace.html")
        self.assertTrue(page.is_file())

    def test_the_agent_s_own_file_name_does_not_survive(self):
        """`pi --export` names the file after itself; the archive names it after the
        session, so a reader finds the page by looking at the jsonl beside it."""
        o = output()
        o.archive_sessions("abcd1234", session_dir("trace.jsonl"))
        session = o.sessions("abcd1234")[0]
        agent.export_html(session, session.parent)
        self.assertFalse((session.parent / "pi-session-trace.html").exists())

    def test_the_page_is_self_contained(self):
        """It must open from a published archive, on a machine with no network."""
        import re

        o = output()
        o.archive_sessions("abcd1234", session_dir("trace.jsonl"))
        session = o.sessions("abcd1234")[0]
        text = agent.export_html(session, session.parent).read_text()
        self.assertTrue(text.startswith("<!DOCTYPE html>"))
        self.assertEqual(re.findall(r'(?:src|href)="https?://[^"]*"', text), [])

    def test_a_session_that_is_not_one_is_reported_not_raised_bare(self):
        directory = Path(tempfile.mkdtemp())
        broken = directory / "broken.jsonl"
        broken.write_text("this is not a session\n")
        with self.assertRaises(RuntimeError):
            agent.export_html(broken, directory)


class TestRenderHtml(unittest.TestCase):
    """`render --html` regenerates the pages without remeasuring anything."""

    def measured(self, *ids: str) -> Path:
        """An output tree with measures, an incomplete ledger, and no session archived.

        Incomplete on purpose. It is the state in which no synthesis is written, and it
        is the state in which somebody most wants to read a trace - so it is the state
        the export has to work in.
        """
        from trysquare.scenario import load

        directory = Path(tempfile.mkdtemp())
        o = outputs.Output(directory, load(SCENARIO), repetitions=1)
        o.prepare()
        o.write_state(o.initial_state())
        o.write_measures(
            [Run(id=i, cell="nothing / off", repetition=0, state=VALID) for i in ids]
        )
        return directory

    def quietly(self, argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(argv)
        return code, out.getvalue()

    def test_runs_without_an_archived_session_are_counted_and_named(self):
        """An old output tree must not answer with a silence that reads as success.

        The agent is mocked as present rather than skipped over: what is under test is the
        reporting, which has nothing to do with whether `pi` is installed, and a test that
        only runs on the author's machine guards nothing on CI.
        """
        from unittest import mock

        directory = self.measured("aaaa1111", "bbbb2222")
        with mock.patch.object(agent, "available", return_value=True):
            code, text = self.quietly(
                ["render", SCENARIO, "-o", str(directory), "--repetitions", "1", "--html"]
            )
        self.assertEqual(code, 0)
        self.assertIn("0 session pages written", text)
        self.assertIn("2 of 2 runs without an archived session", text)

    @unittest.skipUnless(agent.available(), f"{agent.PI!r} is not on PATH")
    def test_each_archived_session_becomes_a_page_in_its_run_directory(self):
        directory = self.measured("aaaa1111", "bbbb2222")
        from trysquare.scenario import load

        o = outputs.Output(directory, load(SCENARIO), repetitions=1)
        o.archive_sessions("aaaa1111", session_dir("first.jsonl", "second.jsonl"))
        o.archive_sessions("bbbb2222", session_dir("only.jsonl"))

        code, text = self.quietly(
            ["render", SCENARIO, "-o", str(directory), "--repetitions", "1", "--html"]
        )
        self.assertEqual(code, 0)
        self.assertIn("3 session pages written", text)
        self.assertNotIn("no archived session", text)
        for run_id, names in (("aaaa1111", ("first", "second")), ("bbbb2222", ("only",))):
            for name in names:
                page = o.directory / "runs" / run_id / outputs.SESSION / f"{name}.html"
                self.assertTrue(page.is_file(), f"{page} should have been written")

    @unittest.skipUnless(agent.available(), f"{agent.PI!r} is not on PATH")
    def test_an_incomplete_matrix_still_gets_its_pages(self):
        """Which is the reason the export runs before the table rather than after it.

        No synthesis is published for an incomplete matrix, and that must not take the
        traces down with it: an incomplete matrix is when a trace is most wanted.
        """
        directory = self.measured("aaaa1111")
        from trysquare.scenario import load

        o = outputs.Output(directory, load(SCENARIO), repetitions=1)
        o.archive_sessions("aaaa1111", session_dir("only.jsonl"))
        code, text = self.quietly(
            ["render", SCENARIO, "-o", str(directory), "--repetitions", "1", "--html"]
        )
        self.assertEqual(code, 0)
        self.assertIn("1 session page written", text)
        self.assertIn("This matrix is incomplete", text)
        self.assertFalse((o.directory / "synthesis.md").exists())

    def test_an_absent_agent_refuses_with_a_message(self):
        from unittest import mock

        directory = self.measured("aaaa1111")
        with mock.patch.object(agent, "available", return_value=False):
            code, text = self.quietly(
                ["render", SCENARIO, "-o", str(directory), "--repetitions", "1", "--html"]
            )
        self.assertEqual(code, 1)
        self.assertIn("is not on PATH", text)

    def test_without_the_flag_nothing_is_exported(self):
        """The flag exists because a rendering must not pay for what was not asked."""
        directory = self.measured("aaaa1111")
        code, text = self.quietly(
            ["render", SCENARIO, "-o", str(directory), "--repetitions", "1"]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("session pages", text)


if __name__ == "__main__":
    unittest.main()
