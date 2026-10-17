# SPDX-License-Identifier: BSD-3-Clause
"""A run's session, drawn while the agent writes it.

What matters most is what the view must never do: write into the session of a run being
measured, find a session anywhere but where the launch said, or start a `pi` per look.

Nothing here spends a token: the agent that draws is a fake.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from trysquare import agent, live, peek

#: Draws a page saying how many lines it was given, and repairs a cut last line the way
#: `pi` does, by appending a newline to the file it was handed.
FAKE_PI = f"""#!{sys.executable}
import os, sys
src, dst = sys.argv[sys.argv.index("--export") + 1:][:2]
with open(os.environ["EXPORTS"], "a") as log:
    log.write(src + "\\n")
if os.environ.get("FAIL"):
    sys.exit(print("cannot read this session", file=sys.stderr) or 1)
data = open(src, "rb").read()
if data and not data.endswith(b"\\n"):
    open(src, "ab").write(b"\\n")
lines = data.count(b"\\n")
open(dst, "w").write(f"<html><body><p>{{lines}} lines</p></body></html>")
"""


@pytest.fixture
def fake_pi(tmp_path, monkeypatch):
    """The `pi` that draws, and the log of what it was asked to draw."""
    fake = tmp_path / "pi"
    fake.write_text(FAKE_PI)
    fake.chmod(0o755)
    monkeypatch.setattr(agent, "PI", str(fake))
    log = tmp_path / "exports.log"
    monkeypatch.setenv("EXPORTS", str(log))
    return lambda: log.read_text().splitlines() if log.exists() else []


def session_dir(tmp_path: Path, *sessions: tuple[str, bytes]) -> Path:
    directory = tmp_path / "work" / "run" / "session"
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in sessions:
        (directory / name).write_bytes(content)
    return directory


def entry(session: Path) -> dict:
    return {"cell": "a", "state": live.RUNNING, "session": str(session)}


LINE = b'{"type":"message"}\n'
MESSAGE = b'{"type":"message","id":"m1","parentId":"s1"}\n'


class TestTheSessionIsTheOneTheLaunchNamed:
    def test_a_running_run_is_found_by_its_id(self, tmp_path):
        e = entry(tmp_path)
        assert peek.in_flight({"finished": None, "runs": {"abc": e}}, "abc") is e

    @pytest.mark.parametrize("run_id", ["nope", "../abc", "abc/..", "", "/etc/passwd"])
    def test_an_id_is_a_key_and_nothing_else(self, tmp_path, run_id):
        assert peek.in_flight({"finished": None, "runs": {"abc": entry(tmp_path)}}, run_id) is None

    def test_a_run_no_longer_running_has_nothing_live_to_show(self, tmp_path):
        stopped = entry(tmp_path) | {"state": live.STOPPED}
        assert peek.in_flight({"runs": {"abc": stopped}}, "abc") is None
        ended = {"finished": time.time(), "runs": {"abc": entry(tmp_path)}}
        assert peek.in_flight(ended, "abc") is None

    def test_a_launch_that_named_no_session_has_none_to_show(self, tmp_path):
        older = {"cell": "a", "state": live.RUNNING}
        assert peek.in_flight({"runs": {"abc": older}}, "abc") is None
        assert peek.in_flight(None, "abc") is None

    def test_the_attempt_shown_is_the_last_one_begun(self, tmp_path):
        """`pi` names a session by the moment it began, and a retry begins later."""
        first, second = "2026-10-08T05-06-09-770Z_a.jsonl", "2026-10-08T05-09-00-001Z_b.jsonl"
        d = session_dir(tmp_path, (second, LINE), (first, LINE * 9))
        assert peek.latest(d) == d / second

    def test_no_session_yet_is_an_answer(self, tmp_path):
        assert peek.latest(tmp_path / "missing") is None
        assert peek.latest(session_dir(tmp_path)) is None
        assert peek.stamp(None) is None


class TestTheAgentsFileIsOnlyRead:
    def test_a_cut_last_line_is_left_out_of_the_copy(self, tmp_path):
        d = session_dir(tmp_path, ("s.jsonl", LINE * 2 + b'{"type":"mes'))
        copy = peek.snapshot(d / "s.jsonl", tmp_path)
        assert copy.read_bytes() == LINE * 2

    def test_a_pi_that_repairs_what_it_reads_never_reaches_the_run(self, tmp_path, fake_pi):
        """`pi` appends a newline to a session whose last line is cut. On the live file
        that newline would land in the middle of the agent's next message."""
        d = session_dir(tmp_path, ("s.jsonl", LINE + b'{"type":"mes'))
        before = ((d / "s.jsonl").read_bytes(), (d / "s.jsonl").stat().st_mtime_ns)
        page = peek.Sessions().page("abc", entry(d), {})
        assert b"1 lines" in page
        assert ((d / "s.jsonl").read_bytes(), (d / "s.jsonl").stat().st_mtime_ns) == before
        assert all(Path(src).parent != d for src in fake_pi())


class TestTheMessageBeingWritten:
    """Drawn under the session's last line, until the session holds it."""

    def drafted(self, tmp_path: Path, *ahead: int) -> tuple[Path, Path]:
        """A session, and a draft written `ahead` nanoseconds after it."""
        d = session_dir(tmp_path, ("s.jsonl", b'{"type":"session","id":"s1"}\n' + MESSAGE))
        draft = d.parent / live.DRAFT
        draft.write_text(json.dumps([{"type": "thinking", "thinking": "hmm"}]))
        at = (d / "s.jsonl").stat().st_mtime_ns + (ahead[0] if ahead else 1)
        os.utime(draft, ns=(at, at))
        return d, draft

    def test_it_ends_the_copy_as_the_line_pi_will_write(self, tmp_path):
        d, draft = self.drafted(tmp_path)
        *_, last = peek.snapshot(d / "s.jsonl", tmp_path, draft).read_text().splitlines()
        line = json.loads(last)
        assert line["parentId"] == "m1"
        assert line["message"]["role"] == "assistant"
        assert line["message"]["content"] == [{"type": "thinking", "thinking": "hmm"}]

    def test_one_the_session_caught_up_with_is_left_out(self, tmp_path):
        """The message ended: its line is in the session, and drawing the draft would
        show it twice."""
        d, draft = self.drafted(tmp_path, -1)
        copy = peek.snapshot(d / "s.jsonl", tmp_path, draft)
        assert copy.read_bytes() == (d / "s.jsonl").read_bytes()
        assert peek.stamp(d / "s.jsonl", draft) == peek.stamp(d / "s.jsonl")

    def test_one_that_vanished_is_left_out(self, tmp_path):
        d, draft = self.drafted(tmp_path)
        assert peek.pending((d / "s.jsonl").read_bytes(), draft.with_name("gone")) == b""

    def test_the_page_moves_while_the_message_grows(self, tmp_path, fake_pi):
        d, draft = self.drafted(tmp_path)
        e = entry(d) | {"draft": str(draft)}
        sessions = peek.Sessions()
        before = sessions.state("abc", e)
        assert b"3 lines" in sessions.page("abc", e, {})
        self.drafted(tmp_path, 2)
        assert sessions.state("abc", e) != before
        sessions.page("abc", e, {})
        assert len(fake_pi()) == 2

    def test_a_subagent_is_drawn_without_it(self, tmp_path):
        assert peek.draft({"draft": "/w/draft.json"}, "sub") is None
        assert peek.draft({"draft": "/w/draft.json"}, None) == Path("/w/draft.json")


class TestThePage:
    def test_the_drawn_session_carries_its_refresher(self, tmp_path, fake_pi):
        d = session_dir(tmp_path, ("s.jsonl", LINE * 3))
        page = peek.Sessions().page("abc", entry(d), {})
        assert b"3 lines" in page
        assert json.dumps(peek.stamp(d / "s.jsonl")).encode() in page
        assert page.index(b"trysquare-live") < page.index(b"</body>")

    def test_before_the_first_exchange_the_page_waits_for_one(self, tmp_path, fake_pi):
        page = peek.Sessions().page("abc", entry(session_dir(tmp_path)), {})
        assert b"Waiting for the session" in page
        assert b"stamp = null" in page
        assert fake_pi() == []

    def test_a_session_pi_cannot_draw_says_so(self, tmp_path, fake_pi, monkeypatch):
        monkeypatch.setenv("FAIL", "1")
        d = session_dir(tmp_path, ("s.jsonl", LINE))
        page = peek.Sessions().page("abc", entry(d), {})
        assert b"could not be rendered" in page
        assert b"pi said: cannot read this session" in page

    def test_a_backend_that_cannot_run_says_so(self, tmp_path, fake_pi):
        """Docker without an image to run, as a launch from before the header named it."""
        d = session_dir(tmp_path, ("s.jsonl", LINE))
        page = peek.Sessions().page("abc", entry(d), {"isolation": {"backend": "docker"}})
        assert b"could not be rendered" in page
        assert b"declares no image" in page
        assert b"pi said" not in page

    def test_a_run_never_drawn_here_has_no_last_state(self, tmp_path, fake_pi):
        """The id would otherwise be the only thing naming where to look."""
        assert peek.Sessions().last("abc", {}) is None

    def test_a_run_that_ended_without_a_session_says_so(self, tmp_path, fake_pi):
        sessions = peek.Sessions()
        d = session_dir(tmp_path)
        sessions.page("abc", entry(d), {})
        page = sessions.last("abc", {})
        assert b"ended before its agent wrote a session" in page
        assert b"ended = true" in page

    def test_a_run_id_cannot_inject_into_the_script(self):
        script = peek.refresher("</script><script>alert(1)//", None)
        assert b"</script><script>" not in script


class TestDrawnWithWhatRan:
    @pytest.fixture
    def drawn_in(self, monkeypatch, machine_answers):
        """The backend each render was handed."""
        backends = []

        def export(session, target, confinement, timeout):
            backends.append(confinement)
            (target / "page.html").write_text("<html><body></body></html>")
            return target / "page.html"

        monkeypatch.setattr(agent, "export_html", export)
        return backends

    def test_the_backend_is_the_one_the_launch_recorded(self, tmp_path, drawn_in):
        d = session_dir(tmp_path, ("s.jsonl", LINE))
        header = {"isolation": {"backend": "bwrap", "bind": ["/opt/node"]}, "image": ""}
        peek.Sessions().page("abc", entry(d), header)
        (backend,) = drawn_in
        assert (backend.name, backend.bind) == ("bwrap", (Path("/opt/node"),))

    def test_a_header_cannot_hand_a_render_a_variable(self, tmp_path, drawn_in):
        d = session_dir(tmp_path, ("s.jsonl", LINE))
        header = {"isolation": {"backend": "bwrap", "env": ["HOME"]}, "image": ""}
        peek.Sessions().page("abc", entry(d), header)
        assert drawn_in[0].env == ()

    def test_a_launch_that_predates_the_header_ran_here(self, tmp_path, drawn_in):
        peek.Sessions().page("abc", entry(session_dir(tmp_path, ("s.jsonl", LINE))), {})
        assert drawn_in[0].name == "none"

    def test_the_operator_s_files_cannot_stop_a_render(
        self, tmp_path, drawn_in, unreadable_operator_files
    ):
        d = session_dir(tmp_path, ("s.jsonl", LINE))
        header = {"isolation": {"backend": "docker"}, "image": "sha256:0"}
        page = peek.Sessions().page("abc", entry(d), header)
        assert b"could not be rendered" not in page
        assert drawn_in[0].name == "docker"

    def test_a_backend_it_cannot_build_says_so(self, tmp_path, drawn_in):
        d = session_dir(tmp_path, ("s.jsonl", LINE))
        page = peek.Sessions().page("abc", entry(d), {"isolation": {"backend": "vm"}})
        assert b"could not be rendered" in page


class TestAPageIsDrawnOncePerChange:
    def test_an_unchanged_session_is_not_drawn_again(self, tmp_path, fake_pi):
        d = session_dir(tmp_path, ("s.jsonl", LINE))
        sessions = peek.Sessions()
        first = sessions.page("abc", entry(d), {})
        assert sessions.page("abc", entry(d), {}) == first
        assert len(fake_pi()) == 1

    def test_a_session_that_grew_is(self, tmp_path, fake_pi):
        d = session_dir(tmp_path, ("s.jsonl", LINE))
        sessions = peek.Sessions()
        sessions.page("abc", entry(d), {})
        with (d / "s.jsonl").open("ab") as f:
            f.write(LINE)
        assert b"2 lines" in sessions.page("abc", entry(d), {})
        assert len(fake_pi()) == 2

    def test_a_failure_is_kept_too(self, tmp_path, fake_pi, monkeypatch):
        """Or every look of every reader would start a `pi` that fails the same way."""
        monkeypatch.setenv("FAIL", "1")
        d = session_dir(tmp_path, ("s.jsonl", LINE))
        sessions = peek.Sessions()
        sessions.page("abc", entry(d), {})
        sessions.page("abc", entry(d), {})
        assert len(fake_pi()) == 1

    def test_a_run_that_left_is_forgotten(self, tmp_path, fake_pi):
        d = session_dir(tmp_path, ("s.jsonl", LINE))
        sessions = peek.Sessions()
        sessions.page("abc", entry(d), {})
        sessions.forget({})
        sessions.page("abc", entry(d), {})
        assert len(fake_pi()) == 2


class TestNoExportStorm:
    @pytest.fixture
    def slow_export(self, monkeypatch):
        """An export that takes a while, and the most of them ever seen at once."""
        seen = {"now": 0, "most": 0, "calls": 0}
        lock = threading.Lock()

        def export(session, target, confinement, timeout):
            with lock:
                seen["now"] += 1
                seen["calls"] += 1
                seen["most"] = max(seen["most"], seen["now"])
            time.sleep(0.2)
            with lock:
                seen["now"] -= 1
            page = target / "page.html"
            page.write_text("<html><body></body></html>")
            return page

        monkeypatch.setattr(agent, "export_html", export)
        return seen

    @staticmethod
    def together(*looks) -> None:
        threads = [threading.Thread(target=look) for look in looks]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def test_many_readers_of_one_run_cost_one_export(self, tmp_path, slow_export):
        d = session_dir(tmp_path, ("s.jsonl", LINE))
        sessions = peek.Sessions()
        self.together(*[lambda: sessions.page("abc", entry(d), {})] * 6)
        assert slow_export["calls"] == 1

    def test_many_runs_are_drawn_a_few_at_a_time(self, tmp_path, slow_export):
        sessions = peek.Sessions()
        looks = []
        for i in range(6):
            d = session_dir(tmp_path / str(i), ("s.jsonl", LINE))
            looks.append(lambda i=i, d=d: sessions.page(str(i), entry(d), {}))
        self.together(*looks)
        assert slow_export["calls"] == 6
        assert slow_export["most"] <= peek.EXPORTS
