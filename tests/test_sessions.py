"""Archiving the agent's own traces, and rendering them back as HTML.

The traces were always jsonl, and they were always written where the OS is free to
delete them: under a work directory the documentation itself calls disposable. So the
only durable evidence of what an agent did was the diff it left behind, while the output
tree's own layout claimed to hold `runs/<id>/session/*.jsonl`.

Three things are guarded here. That the archive keeps *this* launch's sessions and not a
previous one's. That the agent never sees a previous launch's sessions either - the work
directory is keyed by a stable run id, so they would sit where it writes its own. And
that `render --html` says what it did, including when there was nothing to do.

Nothing here spends a token. The one test that runs the agent runs it on a fixture, in
`--export` mode, which reads a file and returns.
"""

import itertools
import re
import shutil
from pathlib import Path

import pytest

from trysquare import agent, outputs
from trysquare.cli import main, session_links
from trysquare.measure import VALID, Run
from trysquare.scenario import load, parse

from tests.gitrepo import a_repo
from tests.spy import launch
from tests.test_scenario import GRID

FIXTURE = Path(__file__).parent / "fixtures" / "session-minimal.jsonl"
ROOT = Path(__file__).resolve().parent.parent
SCENARIO = str(ROOT / "tests" / "fixtures" / "matrix.toml")

needs_the_agent = pytest.mark.skipif(
    shutil.which(agent.PI) is None, reason=f"{agent.PI!r} is not on PATH"
)


@pytest.fixture
def output(tmp_path) -> outputs.Output:
    directory = tmp_path / "out"
    directory.mkdir()
    return outputs.Output(directory, parse(GRID))


@pytest.fixture
def session_dir(tmp_path):
    """A work directory holding the named sessions, with the fixture as content.

    A factory rather than a directory: several tests archive twice, and the second
    call has to land somewhere the first one did not.
    """
    launches = itertools.count()

    def make(*names: str) -> Path:
        directory = tmp_path / f"work{next(launches)}" / "session"
        directory.mkdir(parents=True)
        for name in names:
            (directory / name).write_bytes(FIXTURE.read_bytes())
        return directory

    return make


@pytest.fixture
def measured(tmp_path):
    """An output tree with measures, an incomplete ledger, and no session archived.

    Incomplete on purpose. It is the state in which no synthesis is written, and it
    is the state in which somebody most wants to read a trace - so it is the state
    the export has to work in.
    """

    def make(*ids: str) -> Path:
        directory = tmp_path / "measured"
        directory.mkdir()
        o = outputs.Output(directory, load(SCENARIO), repetitions=1)
        o.prepare()
        o.write_state(o.initial_state())
        o.write_measures([Run(id=i, cell="nothing / off", repetition=0, state=VALID) for i in ids])
        return directory

    return make


FLOW = "runs/2026-10-08_04-35-14"


def archive_a_flow(o: outputs.Output, run_id: str, tmp_path: Path) -> None:
    """A combo run directory, archived as a `/run` leaves it: no session of pi's own."""
    clone = tmp_path / "clone"
    for name in ("journal.jsonl", "fix/fixer.jsonl", "fix/fixer.html", "review/reviewer.html"):
        (clone / FLOW / name).parent.mkdir(parents=True, exist_ok=True)
        (clone / FLOW / name).write_text("{}\n")
    o.archive_flows(run_id, clone, [FLOW])


def rendered(capsys, argv) -> tuple[int, str]:
    """`render`, and everything it said on either stream."""
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


class TestWriteJson:
    def test_a_rewrite_of_the_same_data_is_byte_identical(self, output):
        """What lets `replay --rescore` promise to leave untouched what it did not change."""
        output.prepare()
        runs = [Run(id="aa", cell="nothing / off", repetition=0, usage={"cost": 0.5}, state=VALID)]
        first = output.write_measures(runs).read_bytes()
        assert output.write_measures(runs).read_bytes() == first


class TestArchiving:
    def test_sessions_land_beside_the_run(self, output, session_dir):
        copied = output.archive_sessions("abcd1234", session_dir("one.jsonl", "two.jsonl"))
        assert [p.name for p in copied] == ["one.jsonl", "two.jsonl"]
        assert copied[0].parent == output.directory / "runs" / "abcd1234" / outputs.SESSION

    def test_the_content_is_copied_verbatim(self, output, session_dir):
        """A trace that is not byte-for-byte the agent's own is not a trace."""
        copied = output.archive_sessions("abcd1234", session_dir("one.jsonl"))
        assert copied[0].read_bytes() == FIXTURE.read_bytes()

    def test_a_relaunch_replaces_the_archive_rather_than_adding_to_it(self, output, session_dir):
        """Relaunching an experiment overwrites it, so the archive must overwrite too.

        Otherwise the previous launch's session is attributed to this one: the file count
        stops matching `attempts`, and two measurements sit under one name.
        """
        output.archive_sessions("abcd1234", session_dir("first.jsonl"))
        output.archive_sessions("abcd1234", session_dir("second.jsonl"))
        assert [p.name for p in output.sessions("abcd1234")] == ["second.jsonl"]

    def test_a_stale_page_does_not_survive_a_relaunch(self, output, session_dir):
        """A page rendered from the previous trace would sit there looking current."""
        output.archive_sessions("abcd1234", session_dir("first.jsonl"))
        stale = output.sessions("abcd1234")[0].with_suffix(".html")
        stale.write_text("<!DOCTYPE html>")
        output.archive_sessions("abcd1234", session_dir("second.jsonl"))
        assert not stale.exists()

    def test_a_launch_that_produced_nothing_clears_the_archive(self, output, session_dir):
        """A run whose agent never started must not inherit the last trace."""
        output.archive_sessions("abcd1234", session_dir("first.jsonl"))
        output.archive_sessions("abcd1234", session_dir())
        assert output.sessions("abcd1234") == []

    def test_an_absent_session_directory_is_not_an_error(self, output):
        """The agent may have failed to start. That is a run to record, not a crash."""
        assert output.archive_sessions("abcd1234", Path("/nonexistent/session")) == []

    def test_nothing_to_archive_creates_no_run_directory(self, output, session_dir):
        """An empty `runs/<id>/` would read as a run that left something behind."""
        output.archive_sessions("abcd1234", session_dir())
        assert not (output.directory / "runs" / "abcd1234").exists()

    def test_sessions_of_a_run_that_has_none(self, output):
        assert output.sessions("abcd1234") == []


# An agent that records what its session directory holds when it starts, then leaves a
# session of its own and changes the tree, so the run counts as a measurement.
class TestALaunchStartsBlind:
    """The work directory is keyed by a stable run id, so a relaunch finds the previous
    launch's session where the agent writes its own. The agent can read it, and one did:
    it found its predecessors' reasoning on the very task it was measured on.
    """

    def test_a_relaunch_hides_the_previous_session_from_the_agent(self, tmp_path, monkeypatch):
        source = a_repo({"a.js": "one\n"})
        launch(tmp_path, monkeypatch, source)
        seen = launch(tmp_path, monkeypatch, source)
        assert [s["sessions"] for s in seen] == [[]] * 4, "each run starts from nothing"


@needs_the_agent
class TestExport:
    """The agent renders its own sessions, so nothing here reimplements its format."""

    def test_the_page_sits_beside_the_jsonl_under_the_same_stem(self, output, session_dir):
        output.archive_sessions("abcd1234", session_dir("trace.jsonl"))
        session = output.sessions("abcd1234")[0]
        page = agent.export_html(session, session.parent)
        assert page == session.parent / "trace.html"
        assert page.is_file()

    def test_the_agent_s_own_file_name_does_not_survive(self, output, session_dir):
        """`pi --export` names the file after itself; the archive names it after the
        session, so a reader finds the page by looking at the jsonl beside it."""
        output.archive_sessions("abcd1234", session_dir("trace.jsonl"))
        session = output.sessions("abcd1234")[0]
        agent.export_html(session, session.parent)
        assert not (session.parent / "pi-session-trace.html").exists()

    def test_the_page_is_self_contained(self, output, session_dir):
        """It must open from a published archive, on a machine with no network."""
        output.archive_sessions("abcd1234", session_dir("trace.jsonl"))
        session = output.sessions("abcd1234")[0]
        text = agent.export_html(session, session.parent).read_text()
        assert text.startswith("<!DOCTYPE html>")
        assert re.findall(r'(?:src|href)="https?://[^"]*"', text) == []

    def test_a_session_that_is_not_one_is_reported_not_raised_bare(self, tmp_path):
        broken = tmp_path / "broken.jsonl"
        broken.write_text("this is not a session\n")
        with pytest.raises(RuntimeError):
            agent.export_html(broken, tmp_path)


class TestRenderHtml:
    """`render --html` regenerates the pages without remeasuring anything."""

    def test_runs_without_an_archived_session_are_counted_and_named(
        self, measured, capsys, monkeypatch
    ):
        """An old output tree must not answer with a silence that reads as success.

        The agent is reported present rather than skipped over: what is under test is the
        reporting, which has nothing to do with whether `pi` is installed, and a test that
        only runs on the author's machine guards nothing on CI.
        """
        directory = measured("aaaa1111", "bbbb2222")
        monkeypatch.setattr(agent, "unrunnable", lambda *_: None)
        code, text = rendered(
            capsys,
            ["render", SCENARIO, "-o", str(directory), "--repetitions", "1", "--html"],
        )
        assert code == 0
        assert "0 session pages written" in text
        assert "2 of 2 runs without an archived session" in text

    def test_a_flow_run_is_not_reported_without_a_session(
        self, measured, capsys, monkeypatch, tmp_path
    ):
        """combo already wrote one page per subagent: a flow run has nothing to export."""
        directory = measured("aaaa1111", "bbbb2222")
        archive_a_flow(
            outputs.Output(directory, load(SCENARIO), repetitions=1), "aaaa1111", tmp_path
        )
        monkeypatch.setattr(agent, "unrunnable", lambda *_: None)
        code, text = rendered(
            capsys,
            ["render", SCENARIO, "-o", str(directory), "--repetitions", "1", "--html"],
        )
        assert code == 0
        assert "1 of 2 runs without an archived session" in text
        assert "1 combo flow run, whose subagent pages combo wrote itself" in text

    @needs_the_agent
    def test_each_archived_session_becomes_a_page_in_its_run_directory(
        self, measured, session_dir, capsys
    ):
        directory = measured("aaaa1111", "bbbb2222")
        o = outputs.Output(directory, load(SCENARIO), repetitions=1)
        o.archive_sessions("aaaa1111", session_dir("first.jsonl", "second.jsonl"))
        o.archive_sessions("bbbb2222", session_dir("only.jsonl"))

        code, text = rendered(
            capsys,
            ["render", SCENARIO, "-o", str(directory), "--repetitions", "1", "--html"],
        )
        assert code == 0
        assert "3 session pages written" in text
        assert "no archived session" not in text
        for run_id, names in (("aaaa1111", ("first", "second")), ("bbbb2222", ("only",))):
            for name in names:
                page = o.directory / "runs" / run_id / outputs.SESSION / f"{name}.html"
                assert page.is_file(), f"{page} should have been written"

    @needs_the_agent
    def test_a_relative_output_gets_every_page(self, measured, session_dir, capsys, monkeypatch):
        """The agent writes the page where its resolved target is, so the page has to be
        named relative to a resolved output too."""
        directory = measured("aaaa1111")
        o = outputs.Output(directory, load(SCENARIO), repetitions=1)
        o.archive_sessions("aaaa1111", session_dir("first.jsonl", "second.jsonl"))
        monkeypatch.chdir(directory.parent)
        code, text = rendered(
            capsys,
            ["render", SCENARIO, "-o", directory.name, "--repetitions", "1", "--html"],
        )
        assert code == 0
        assert "2 session pages written" in text

    @needs_the_agent
    def test_an_incomplete_matrix_still_gets_its_pages(self, measured, session_dir, capsys):
        """Which is the reason the export runs before the table rather than after it.

        No synthesis is published for an incomplete matrix, and that must not take the
        traces down with it: an incomplete matrix is when a trace is most wanted.
        """
        directory = measured("aaaa1111")
        o = outputs.Output(directory, load(SCENARIO), repetitions=1)
        o.archive_sessions("aaaa1111", session_dir("only.jsonl"))
        code, text = rendered(
            capsys,
            ["render", SCENARIO, "-o", str(directory), "--repetitions", "1", "--html"],
        )
        assert code == 0
        assert "1 session page written" in text
        assert "This matrix is incomplete" in text
        assert not (o.directory / "synthesis.md").exists()

    def test_an_absent_agent_refuses_with_a_message(self, measured, capsys, monkeypatch):
        directory = measured("aaaa1111")
        monkeypatch.setattr(agent, "PI", "/nonexistent/pi")
        code, text = rendered(
            capsys,
            ["render", SCENARIO, "-o", str(directory), "--repetitions", "1", "--html"],
        )
        assert code == 1
        assert (
            "'/nonexistent/pi' does not run on this machine, so no session can be exported" in text
        )

    def test_without_the_flag_nothing_is_exported(self, measured, capsys):
        """The flag exists because a rendering must not pay for what was not asked."""
        directory = measured("aaaa1111")
        code, text = rendered(
            capsys, ["render", SCENARIO, "-o", str(directory), "--repetitions", "1"]
        )
        assert code == 0
        assert "session pages" not in text


class TestSessionLinks:
    def test_a_flow_links_each_subagent_page_combo_wrote(self, measured, tmp_path):
        directory = measured("aaaa1111")
        o = outputs.Output(directory, load(SCENARIO), repetitions=1)
        archive_a_flow(o, "aaaa1111", tmp_path)
        ((_, _, _, pages),) = session_links(o, o.read_measures())
        assert pages == [
            ("fix/fixer, attempt 1", f"{FLOW}/fix/fixer.html"),
            ("review/reviewer, attempt 1", f"{FLOW}/review/reviewer.html"),
        ]

    def test_a_session_page_is_linked_by_its_attempt(self, measured, session_dir):
        directory = measured("aaaa1111")
        o = outputs.Output(directory, load(SCENARIO), repetitions=1)
        o.archive_sessions("aaaa1111", session_dir("one.jsonl", "two.jsonl"))
        for page in o.sessions("aaaa1111"):
            page.with_suffix(".html").write_text("<!doctype html>")
        ((_, _, _, pages),) = session_links(o, o.read_measures())
        assert pages == [("attempt 1", "one.html"), ("attempt 2", "two.html")]
