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

from . import agent
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
    prepared once, on the first render that needs it.
    """

    def __init__(self) -> None:
        self._backends: dict[str, object] = {}
        self._pages: dict[str, tuple[tuple, bytes]] = {}
        self._dirs: dict[str, str] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(EXPORTS)

    def page(self, run_id: str, entry: dict, live: dict) -> bytes:
        """What `/run/<id>` shows of a run in flight: its session, or why not yet. `live`
        is the `live.json` it is in flight in."""
        with self._lock:
            self._dirs[run_id] = entry["session"]
        return self._draw(run_id, entry["session"], live, ended=False)

    def last(self, run_id: str, live: dict | None) -> bytes | None:
        """The last state of a run drawn here that has since ended, or None for one that
        never was. Its final message lands just before it leaves `live.json`, so a page
        that stopped at its last look would miss the one most worth reading."""
        directory = self._dirs.get(run_id)
        return self._draw(run_id, directory, live or {}, ended=True) if directory else None

    def forget(self, keep) -> None:
        """Drops the pages kept for runs no longer in flight."""
        with self._lock:
            for run_id in set(self._pages) - set(keep):
                self._pages.pop(run_id, None)
                self._locks.pop(run_id, None)

    def _draw(self, run_id: str, directory: str, live: dict, ended: bool) -> bytes:
        session = latest(directory)
        current = stamp(session)
        if current is None:
            said = NO_SESSION if ended else WAITING
            return status(*said, refresh=(run_id, None, ended))
        with self._lock_for(run_id):
            kept = self._pages.get(run_id)
            if kept and kept[0] == (current, ended):
                return kept[1]
            with self._slots:
                body = self._render(session, live, (run_id, current, ended))
            self._pages[run_id] = ((current, ended), body)
        return body

    def _lock_for(self, run_id: str) -> threading.Lock:
        with self._lock:
            return self._locks.setdefault(run_id, threading.Lock())

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

    def _render(self, session: Path, live: dict, at: tuple) -> bytes:
        """The exported page with the refresher in it, or a page saying why there is none.

        A failure is kept like a page, under the same stamp, so a session `pi` cannot
        render is tried again when it grows rather than on every look.
        """
        try:
            with tempfile.TemporaryDirectory(prefix="trysquare-peek-") as tmp:
                copy = snapshot(session, Path(tmp))
                if copy is None:
                    return status(*WAITING, refresh=at)
                page = agent.export_html(copy, Path(tmp), self._backend(live), TIMEOUT)
                body = page.read_bytes()
        except (OSError, RuntimeError, ValueError) as e:
            return status(
                "This session could not be rendered",
                f"pi said: {e}",
                refresh=at,
            )
        head, end, tail = body.rpartition(b"</body>")
        return (head + refresher(*at) + end + tail) if end else body


WAITING = (
    "Waiting for the session",
    "The agent writes its session once its first exchange is done.",
)
NO_SESSION = ("No session", "This run ended before its agent wrote a session.")


#: Keeps the page in step with the session: it asks every `REFRESH` seconds whether the
#: session moved, and reloads only when it did, or once more when the run ended. Where the reader was is kept across the
#: reload - at the bottom stays at the bottom, as a terminal would, and anywhere else
#: stays put. The first look starts at the bottom, where the agent is.
SCRIPT = """
<div id="trysquare-live" title="Redrawn as the agent writes its session">live</div>
<style>
#trysquare-live { position: fixed; right: 12px; bottom: 12px; z-index: 1000;
  font: 600 11px/1 ui-sans-serif, system-ui, sans-serif; letter-spacing: .04em;
  padding: 5px 9px 5px 20px; border-radius: 10px; color: #fff; background: #0b7a3b;
  box-shadow: 0 1px 3px rgba(0, 0, 0, .3); pointer-events: none; }
#trysquare-live::before { content: ""; position: absolute; left: 8px; top: 50%%;
  width: 6px; height: 6px; margin-top: -3px; border-radius: 50%%; background: currentColor; }
#trysquare-live.ended { background: #5c5c5c; }
</style>
<script>
(() => {
  const run = %(run)s, stamp = %(stamp)s, ended = %(ended)s, every = %(every)d;
  const key = 'trysquare-scroll:' + run;
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
      const r = await fetch('/run/' + encodeURIComponent(run) + '/state', { cache: 'no-store' });
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


def refresher(run_id: str, current: str | None, ended: bool = False) -> bytes:
    """`SCRIPT` for one run at one stamp, polling only while the run is in flight."""

    def literal(value) -> str:
        return json.dumps(value).replace("</", "<\\/")

    values = {"run": literal(run_id), "stamp": literal(current), "ended": literal(ended)}
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
:root { --bg: #ffffff; --fg: #1a1a1a; --dim: #5c5c5c; }
@media (prefers-color-scheme: dark) { :root { --bg: #15171a; --fg: #e8e8e8; --dim: #9aa0a6; } }
body { margin: 0; padding: 3rem 1.5rem; background: var(--bg); color: var(--fg);
  font: 15px/1.5 ui-sans-serif, system-ui, sans-serif; }
main { max-width: 40rem; margin: 0 auto; }
h1 { font-size: 1.15rem; font-weight: 600; margin: 0 0 .5rem; }
p { color: var(--dim); margin: 0; overflow-wrap: anywhere; }
code { font-family: ui-monospace, monospace; color: var(--fg); }
</style>
</head>
<body>
<main><h1>%(title)s</h1><p>%(message)s</p></main>
%(script)s
</body>
</html>
"""


def status(title: str, message: str, refresh: tuple | None = None) -> bytes:
    """A page saying why there is no session to show, reloading once there is one when
    `refresh` holds `refresher`'s arguments. `code` in `message` is set as code."""
    script = refresher(*refresh).decode() if refresh else ""
    message = re.sub(r"`([^`]+)`", r"<code>\1</code>", html.escape(message))
    values = {"title": html.escape(title), "message": message, "script": script}
    return (STATUS % values).encode()
