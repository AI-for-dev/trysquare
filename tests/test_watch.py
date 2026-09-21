# SPDX-License-Identifier: BSD-3-Clause
"""What `watch` serves, and what it refuses to serve.

A matrix directory holds prompts, diffs and session transcripts. Two properties matter
more than anything the page draws: no request may name a path, and nothing here may
write into the tree it reads. The third is the one the grilling settled - a count while
the matrix is incomplete, and never a verdict.

Nothing here spends a token.
"""

import json
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from trysquare import outputs, watch
from trysquare.measure import EMPTY, VALID, Run


def matrix(tmp_path: Path, runs=(), state=None, live=None, synthesis=False) -> Path:
    """A matrix directory holding only the files a given test needs."""
    directory = tmp_path / "t_etalon-v1_ilaas_gemma-4-31b_n2"
    directory.mkdir(parents=True, exist_ok=True)
    outputs.write_json(directory / outputs.STATE, state or {"runs": {}})
    if live is not None:
        outputs.write_json(directory / outputs.LIVE, live)
    if runs:
        outputs.write_json(directory / outputs.MEASURES, [r.__dict__ for r in runs])
    if synthesis:
        (directory / watch.SYNTHESIS_PAGE).write_text("<html>the verdict</html>")
    return directory


def ledger(**cells) -> dict:
    """A `state.json` holding one entry per (cell, repetition, state) asked for."""
    runs, i = {}, 0
    for cell, states in cells.items():
        for st in states:
            runs[f"r{i}"] = {"cell": cell, "repetition": i, "state": st}
            i += 1
    return {"runs": runs, "provider": "ilaas", "model": "gemma-4-31b", "concurrency": 5}


def measured(cell: str, **metrics) -> Run:
    return Run(
        id=f"{cell}-{len(metrics)}",
        cell=cell,
        repetition=0,
        state=VALID,
        usage={"turns": 1, "input": 1, "output": 1},
        metrics=metrics,
    )


class TestProgress:
    def test_a_missing_run_is_not_a_done_one(self, tmp_path):
        d = matrix(tmp_path, state=ledger(a=["valid", "missing", "empty"]))
        p = watch.assemble(d)["progress"]
        assert (p["planned"], p["done"]) == (3, 2)
        assert p["cells"]["a"] == {"planned": 3, "done": 2}

    def test_an_empty_directory_answers_rather_than_raises(self, tmp_path):
        """A launch writes its ledger before its first run, but a reader may arrive in
        between, and a page that fails to load reads as a broken harness."""
        directory = tmp_path / "bare"
        directory.mkdir()
        assert watch.assemble(directory)["progress"]["planned"] == 0


class TestTallies:
    def test_a_cell_is_a_count_out_of_what_could_say(self, tmp_path):
        """`rate`'s denominator: a metric the validator could not judge is a hole, and
        a hole is not a `false`."""
        runs = [measured("a", ok=True), measured("a", ok=False), measured("a", other=True)]
        d = matrix(tmp_path, runs=runs, state=ledger(a=["valid"] * 3))
        assert watch.assemble(d)["tallies"]["cells"]["a"]["counts"]["ok"] == [1, 2]

    def test_only_what_a_count_can_be_taken_of_becomes_a_column(self, tmp_path):
        """A number has a median rather than a count, and a list has neither."""
        runs = [measured("a", ok=True, touched=["game/x.js"], turns=4)]
        d = matrix(tmp_path, runs=runs, state=ledger(a=["valid"]))
        assert watch.assemble(d)["tallies"]["metrics"] == ["ok"]

    def test_a_run_that_produced_nothing_is_not_counted(self, tmp_path):
        run = Run(id="x", cell="a", repetition=0, state=EMPTY, usage={}, metrics={"ok": True})
        d = matrix(tmp_path, runs=[run], state=ledger(a=["empty"]))
        assert watch.assemble(d)["tallies"]["cells"] == {}


class TestTheVerdictWaits:
    def test_an_incomplete_matrix_is_offered_no_synthesis(self, tmp_path):
        d = matrix(tmp_path, state=ledger(a=["valid", "missing"]) | {"complete": False})
        payload = watch.assemble(d)
        assert payload["complete"] is False
        assert payload["synthesis"] is None

    def test_a_complete_one_is_pointed_at_the_file_that_carries_it(self, tmp_path):
        """Rather than a second computation of the same interval, which is how two
        answers to one question come to disagree."""
        d = matrix(tmp_path, state=ledger(a=["valid"]) | {"complete": True}, synthesis=True)
        payload = watch.assemble(d)
        assert payload["complete"] is True
        assert payload["synthesis"] == watch.SYNTHESIS_PAGE


class TestSilence:
    def test_a_launch_that_stopped_writing_is_called_out(self, tmp_path):
        """`SIGKILL` stamps no ending, so the file goes on claiming its runs are alive.
        The heartbeat is the only thing that tells that from a working launch."""
        old = time.time() - watch.SILENT_AFTER - 5
        d = matrix(tmp_path, live={"seen": old, "finished": None, "runs": {}})
        assert watch.assemble(d)["silent"] is True

    def test_a_fresh_heartbeat_is_not(self, tmp_path):
        d = matrix(tmp_path, live={"seen": time.time(), "finished": None, "runs": {}})
        assert watch.assemble(d)["silent"] is False

    def test_a_launch_that_said_it_finished_is_not_silent_but_over(self, tmp_path):
        old = time.time() - 3600
        d = matrix(tmp_path, live={"seen": old, "finished": old, "runs": {}})
        assert watch.assemble(d)["silent"] is False


@pytest.fixture
def served(tmp_path):
    """One directory, served on a port the system picked, for the length of a test."""
    directory = matrix(tmp_path, state=ledger(a=["valid", "missing"]), synthesis=True)
    httpd = watch.server(directory, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", directory
    finally:
        httpd.shutdown()
        httpd.server_close()


def get(url: str):
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - loopback
        return response.status, response.read()


class TestTheServer:
    def test_it_binds_to_the_loopback_interface_only(self, served):
        """A matrix directory is somebody's work, not something to put on an interface
        because a dashboard was convenient."""
        _, directory = served
        httpd = watch.server(directory, 0)
        try:
            assert httpd.server_address[0] == "127.0.0.1"
        finally:
            httpd.server_close()

    def test_the_page_and_its_data_are_served(self, served):
        url, _ = served
        status, page = get(f"{url}/")
        assert status == 200
        assert b"<html" in page
        status, body = get(f"{url}/data")
        assert json.loads(body)["progress"]["planned"] == 2

    def test_a_url_never_becomes_a_path(self, served):
        """Three routes, named one by one. Nothing joins a request onto the tree."""
        url, directory = served
        (directory / "secret.txt").write_text("the prompt")
        for path in ("/secret.txt", "/state.json", "/../../etc/passwd", "/runs/a/diff.patch"):
            with pytest.raises(urllib.error.HTTPError) as e:
                get(url + path)
            assert e.value.code == 404

    def test_the_synthesis_is_served_when_there_is_one(self, served):
        url, _ = served
        status, body = get(f"{url}/{watch.SYNTHESIS_PAGE}")
        assert (status, b"the verdict" in body) == (200, True)

    def test_serving_writes_nothing_into_the_matrix(self, served):
        url, directory = served
        before = {p: p.stat().st_mtime_ns for p in directory.rglob("*") if p.is_file()}
        get(f"{url}/data")
        get(f"{url}/")
        after = {p: p.stat().st_mtime_ns for p in directory.rglob("*") if p.is_file()}
        assert before == after


class TestTheCommand:
    def test_a_directory_with_no_ledger_is_refused(self, tmp_path, capsys):
        """A wrong path would otherwise open a page saying nothing, and a reader would
        believe the matrix was empty rather than the address."""
        from trysquare.cli import main

        assert main(["watch", str(tmp_path), "--no-open"]) == 1
        assert outputs.STATE in capsys.readouterr().err


def test_the_page_is_shipped_with_the_package():
    """It is read off the package at request time, so an install that lost it serves a
    traceback to a browser instead of a page."""
    assert watch.PAGE.is_file()
    assert Path(tempfile.gettempdir()) not in watch.PAGE.parents
