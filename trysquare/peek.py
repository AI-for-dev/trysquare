# SPDX-License-Identifier: BSD-3-Clause
"""A run's session, rendered while the agent is still writing it.

`watch` follows numbers; this shows the work itself, the page `pi --export` draws of a
session, redrawn as the session grows. The agent appends one line per message to its
session in `--mode json` too, so what is on disk is the run as far as it has gone.

**The agent's file is only ever read.** `pi` repairs a session whose last line is cut by
appending a newline to it, and a live session's last line is cut whenever the agent is
halfway through writing one: rendering it in place would write into the middle of the
next message of a run being measured. So the complete lines are copied out and `pi`
renders the copy, in a directory of this process's own.

**A combo flow is shown by its subagents.** A `/run` adds no message to the run's own
session, so pi never writes it: the run's page lists the sessions combo writes for the
subagents in the clone, each drawn like a session, then their archived copies once the
run ended. A subagent is one the flow holds, picked by its id, never a path.

**Rendered by the agent that writes it**, through the backend and the image the launch
wrote in `live.json`'s header: what ran, whatever the config says now.

**Bounded.** One render per run at a time, `EXPORTS` at most across runs, and none at
all while the session has not changed: a room of people watching costs what one does.
"""

from __future__ import annotations

import html
import json
import re
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote

from . import agent, combo
from .live import RUNNING

#: Renders running at once. Each starts a `pi`, a container under docker.
EXPORTS = 2

#: Seconds a render may take. A session is rendered in well under one; a render that
#: hangs must not hold its slot for the two minutes an archive is given.
TIMEOUT = 30

#: Seconds between two looks at whether the session moved.
REFRESH = 2.5


def in_flight(live: dict | None, run_id: str) -> dict | None:
    """The live entry of `run_id`, or None when no running run has one to show.

    The id is a key looked up in `live.json`, never a path: the session's directory is
    the one the launch wrote there.
    """
    entry = ((live or {}).get("runs") or {}).get(run_id)
    if not entry or not entry.get("session") or entry.get("state") != RUNNING:
        return None
    return None if live.get("finished") else entry


def latest(session_dir: Path) -> Path | None:
    """The session of the attempt in progress. `pi` names each by the moment it began."""
    try:
        return max(Path(session_dir).glob("*.jsonl"), default=None)
    except OSError:
        return None


def stamp(session: Path | None) -> str | None:
    """What changes when the session does, or None while there is none."""
    try:
        st = session.stat() if session else None
    except OSError:
        return None
    return f"{session.name}:{st.st_mtime_ns}:{st.st_size}" if st else None


def snapshot(session: Path, into: Path) -> Path | None:
    """The complete lines of `session`, copied into `into`, or None when there are none."""
    data = session.read_bytes()
    whole = data[: data.rfind(b"\n") + 1]
    if not whole:
        return None
    copy = into / session.name
    copy.write_bytes(whole)
    return copy


class Sessions:
    """The pages of the sessions in flight, rendered when they change and kept until then.

    One per server. A backend is built from what a launch's header says it ran on, and
    prepared once, on the first render that needs it. `archive(run_id)` is the directory
    an ended run's sessions were archived in, or None.
    """

    def __init__(self, archive=lambda run_id: None) -> None:
        self._archive = archive
        self._backends: dict[str, object] = {}
        self._pages: dict[tuple, tuple[tuple, bytes]] = {}
        self._entries: dict[str, dict] = {}
        self._locks: dict[tuple, threading.Lock] = {}
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(EXPORTS)

    def page(self, run_id: str, entry: dict, live: dict, subagent: str | None = None):
        """What `/run/<id>` shows of a run in flight, or `/run/<id>/<subagent>` of one of
        its flow's subagents: a session, or why not yet. None for a subagent the flow does
        not hold. `live` is the `live.json` it is in flight in."""
        with self._lock:
            self._entries[run_id] = entry
        return self._show(run_id, subagent, entry, live, ended=False)

    def last(self, run_id: str, live: dict | None, subagent: str | None = None):
        """The last state of a run drawn here that has since ended, or None for one that
        never was. Its final message lands just before it leaves `live.json`, so a page
        that stopped at its last look would miss the one most worth reading."""
        entry = self._entries.get(run_id)
        return self._show(run_id, subagent, entry, live or {}, ended=True) if entry else None

    def state(self, run_id: str, entry: dict, subagent: str | None = None) -> str | None:
        """What changes when the page of a run in flight does."""
        try:
            target = self._target(run_id, subagent, entry, ended=False)
        except KeyError:
            return None
        return listed(target) if isinstance(target, list) else stamp(target)

    def forget(self, keep) -> None:
        """Drops the pages kept for runs no longer in flight."""
        with self._lock:
            for at in [at for at in self._pages if at[0] not in keep]:
                self._pages.pop(at, None)
                self._locks.pop(at, None)

    def _target(self, run_id: str, subagent: str | None, entry: dict, ended: bool):
        """What a page draws: a subagent's session, the run's own, or while the run has
        none, its flow's subagents. KeyError for a subagent the flow does not hold."""
        found = self._subagents(run_id, entry, ended)
        if subagent is not None:
            return {s["id"]: s["session"] for s in found}[subagent]
        session = latest(entry["session"])
        return found if session is None and found else session

    def _subagents(self, run_id: str, entry: dict, ended: bool) -> list[dict]:
        """The subagents of the run's flow, from the clone, or once the run ended, from
        the copy archived under the same name."""
        directories = [Path(d) for d in (entry.get("combo") or {}).get("directories") or ()]
        if ended:
            archive = self._archive(run_id)
            directories = [archive / combo.RUNS / d.name for d in directories] if archive else []
        return [s for directory in directories for s in combo.subagents(directory)]

    def _show(self, run_id: str, subagent: str | None, entry: dict, live: dict, ended: bool):
        try:
            target = self._target(run_id, subagent, entry, ended)
        except KeyError:
            return None
        if isinstance(target, list):
            return status(
                *FLOW,
                refresh=(address(run_id), listed(target), ended),
                items=[
                    (
                        address(run_id, s["id"]),
                        s["agent"] or "subagent",
                        s["path"] or s["id"][:8],
                        s["running"] and not ended,
                    )
                    for s in target
                ],
            )
        return self._draw((run_id, subagent), target, live, ended)

    def _draw(self, at: tuple, session: Path | None, live: dict, ended: bool) -> bytes:
        """`session` drawn for the page `at`, (run id, subagent), or why it cannot be."""
        current = stamp(session)
        refresh = (address(*at), current, ended, at[1] is not None)
        if current is None:
            said = NO_SESSION if ended else WAITING
            return status(*said, refresh=refresh)
        with self._lock_for(at):
            kept = self._pages.get(at)
            if kept and kept[0] == (current, ended):
                return kept[1]
            with self._slots:
                body = self._render(session, live, refresh)
            self._pages[at] = ((current, ended), body)
        return body

    def _lock_for(self, at: tuple) -> threading.Lock:
        with self._lock:
            return self._locks.setdefault(at, threading.Lock())

    def _backend(self, live: dict):
        """The backend the launch ran on. A header from before it said so ran on this
        machine. Built through `agent.exporter`, so a header can name no variable."""
        isolation, image = live.get("isolation") or {}, live.get("image") or ""
        key = json.dumps([isolation, image], sort_keys=True)
        with self._lock:
            if key not in self._backends:
                confinement = agent.exporter(isolation)
                confinement.prepare(image or None, ())
                self._backends[key] = confinement
            return self._backends[key]

    def _render(self, session: Path, live: dict, refresh: tuple) -> bytes:
        """The exported page with the refresher in it, or a page saying why there is none.

        A failure is kept like a page, under the same stamp, so a session `pi` cannot
        render is tried again when it grows rather than on every look.
        """
        try:
            with tempfile.TemporaryDirectory(prefix="trysquare-peek-") as tmp:
                copy = snapshot(session, Path(tmp))
                if copy is None:
                    return status(*WAITING, refresh=refresh)
                page = agent.export_html(copy, Path(tmp), self._backend(live), TIMEOUT)
                body = page.read_bytes()
        except (OSError, RuntimeError, ValueError) as e:
            return status(
                "This session could not be rendered",
                f"pi said: {e}",
                refresh=refresh,
            )
        head, end, tail = body.rpartition(b"</body>")
        return (head + refresher(*refresh) + end + tail) if end else body


def address(run_id: str, subagent: str | None = None) -> str:
    """The path of a run's page, or of one of its flow's subagents."""
    return f"/run/{quote(run_id, safe='')}" + (f"/{quote(subagent, safe='')}" if subagent else "")


def listed(subagents: list[dict]) -> str:
    """What changes when the list of a flow's subagents does."""
    return json.dumps([[s["id"], s["agent"], s["path"], s["running"]] for s in subagents])


WAITING = (
    "Waiting for the session",
    "The agent writes its session once its first exchange is done.",
)
NO_SESSION = ("No session", "This run ended before its agent wrote a session.")
FLOW = (
    "The flow's subagents",
    "This run is a combo `/run`, which adds no message to the agent's own session, so that "
    "session stays empty by design. Each subagent of the flow writes its own:",
)


#: Keeps the page in step with the session: it asks every `REFRESH` seconds whether the
#: session moved, and reloads only when it did, or once more when the run ended. Where the reader was is kept across the
#: reload - at the bottom stays at the bottom, as a terminal would, and anywhere else
#: stays put. The first look starts at the bottom, where the agent is. A subagent's page
#: links back to the list of its flow's subagents.
SCRIPT = """
<div id="trysquare-bar">%(back)s<div id="trysquare-live" title="Redrawn as the agent writes its session">live</div></div>
<style>
#trysquare-bar { position: fixed; right: 12px; bottom: 12px; z-index: 1000; display: flex; gap: 6px;
  font: 600 11px/1 ui-sans-serif, system-ui, sans-serif; letter-spacing: .04em; }
#trysquare-bar > * { padding: 5px 9px; border-radius: 10px; color: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, .3); }
#trysquare-back { background: #3a3f45; text-decoration: none; }
#trysquare-back:hover { background: #24282c; }
#trysquare-live { position: relative; padding-left: 20px; background: #0b7a3b; pointer-events: none; }
#trysquare-live::before { content: ""; position: absolute; left: 8px; top: 50%%;
  width: 6px; height: 6px; margin-top: -3px; border-radius: 50%%; background: currentColor; }
#trysquare-live.ended { background: #5c5c5c; }
</style>
<script>
(() => {
  const page = %(page)s, stamp = %(stamp)s, ended = %(ended)s, every = %(every)d;
  const key = 'trysquare-scroll:' + page;
  // pi's page scrolls its #content on a narrow screen and the document on a wide one.
  const range = (b) => b.scrollHeight - b.clientHeight;
  const box = () => [document.getElementById('content'), document.scrollingElement]
    .filter(Boolean).reduce((a, b) => (range(b) > range(a) ? b : a));
  const bottom = (b) => range(b) - b.scrollTop < 40;
  function restore() {
    let saved = null;
    try { saved = JSON.parse(sessionStorage.getItem(key)); } catch (e) {}
    const b = box();
    b.scrollTop = !saved || saved.bottom ? b.scrollHeight : saved.top;
  }
  function save() {
    const b = box();
    try {
      sessionStorage.setItem(key, JSON.stringify({ top: b.scrollTop, bottom: bottom(b) }));
    } catch (e) {}
  }
  async function look() {
    try {
      const r = await fetch(page + '/state', { cache: 'no-store' });
      const s = await r.json();
      if (!s.running || s.stamp !== stamp) { save(); location.reload(); return; }
    } catch (e) {}
    setTimeout(look, every);
  }
  addEventListener('load', () => requestAnimationFrame(restore));
  if (!ended) return setTimeout(look, every);
  const pill = document.getElementById('trysquare-live');
  pill.textContent = 'ended';
  pill.className = 'ended';
  pill.title = 'The run has ended: this is its session as it left it';
})();
</script>
"""


def refresher(page: str, current: str | None, ended: bool = False, back: bool = False) -> bytes:
    """`SCRIPT` for the page at path `page`, at one stamp, polling only while its run is in
    flight. `back` for a subagent's page, which links to its run's."""

    def literal(value) -> str:
        return json.dumps(value).replace("</", "<\\/")

    up = html.escape(page.rpartition("/")[0])
    values = {"page": literal(page), "stamp": literal(current), "ended": literal(ended)}
    values["back"] = f'<a id="trysquare-back" href="{up}">&lsaquo; subagents</a>' if back else ""
    values["every"] = REFRESH * 1000
    return (SCRIPT % values).encode()


#: The small page shown in place of a session, in the dashboard's colours.
STATUS = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<style>
:root { --bg: #ffffff; --fg: #1a1a1a; --dim: #5c5c5c; --line: #e2e2e2; --live: #0b7a3b; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #15171a; --fg: #e8e8e8; --dim: #9aa0a6; --line: #2c3036; --live: #4ec27e; }
}
body { margin: 0; padding: 3rem 1.5rem; background: var(--bg); color: var(--fg);
  font: 15px/1.5 ui-sans-serif, system-ui, sans-serif; }
main { max-width: 40rem; margin: 0 auto; }
h1 { font-size: 1.15rem; font-weight: 600; margin: 0 0 .5rem; }
p { color: var(--dim); margin: 0; overflow-wrap: anywhere; }
code { font-family: ui-monospace, monospace; color: var(--fg); }
ul { list-style: none; margin: 1rem 0 0; padding: 0; border-top: 1px solid var(--line); }
li { display: flex; flex-wrap: wrap; align-items: baseline; gap: .25rem .6rem;
  padding: .5rem 0; border-bottom: 1px solid var(--line); }
li a { color: var(--fg); font-weight: 600; }
li code { color: var(--dim); font-size: .85em; overflow-wrap: anywhere; }
li .running { margin-left: auto; color: var(--live); font-size: .8rem; font-weight: 600; }
</style>
</head>
<body>
<main><h1>%(title)s</h1><p>%(message)s</p>%(items)s</main>
%(script)s
</body>
</html>
"""


def status(title: str, message: str, refresh: tuple | None = None, items=()) -> bytes:
    """A page saying why there is no session to show, reloading once there is one when
    `refresh` holds `refresher`'s arguments. `code` in `message` is set as code.

    `items` are the sessions to show instead, as (link, name, where, running)."""
    script = refresher(*refresh).decode() if refresh else ""
    message = re.sub(r"`([^`]+)`", r"<code>\1</code>", html.escape(message))
    rows = "".join(
        f'<li><a href="{html.escape(link)}">{html.escape(name)}</a>'
        f"<code>{html.escape(where)}</code>"
        + ('<span class="running">running</span>' if running else "")
        + "</li>"
        for link, name, where, running in items
    )
    values = {"title": html.escape(title), "message": message, "script": script}
    values["items"] = f"<ul>{rows}</ul>" if rows else ""
    return (STATUS % values).encode()
