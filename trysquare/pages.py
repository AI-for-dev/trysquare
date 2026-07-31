# SPDX-License-Identifier: BSD-3-Clause
"""`synthesis.html`: the same synthesis, readable in a browser, offline, forever.

One self-contained file. No JavaScript, no external stylesheet, no font fetched
from anywhere: an archive is opened years later, on machines that are offline or
behind proxies, and a page that phones home is a page that rots.

This is deliberately **not** a markdown renderer. The input is the synthesis this
harness itself wrote, so the converter reads exactly the shapes `table.py` emits -
headings, pipe tables, bullet lists, paragraphs - and nothing more general. The
narrowness is the guarantee: a drift between what is written and what is rendered
fails a test here instead of surprising a reader.

Pure: strings in, one string out. Writing it is the caller's job.
"""

from __future__ import annotations

import html
import re

# Small on purpose, and honouring the reader's theme without a line of script.
#
# Two things are declared rather than left to the browser, because the browser's
# answer was wrong for this document. A synthesis heads its sections with `###` and
# its subsections with `####`, so the defaults put every section at 18.7px and every
# subsection at exactly body size - indistinguishable from a table header, and the
# structure of the page invisible. And an unstyled link is `#0000EE`, which on the
# dark background is a contrast of 1.99:1 where 4.5 is legible: the session links
# were the only things to click on the page and the only things nobody could read.
STYLE = """\
body { max-width: 60rem; margin: 2rem auto; padding: 0 1rem;
       font: 16px/1.55 system-ui, sans-serif; color: #1a1a1a; background: #ffffff; }
h1, h2, h3, h4 { line-height: 1.25; margin: 2.2rem 0 0.7rem; }
h1 { font-size: 2.1rem; margin-top: 0; }
h2 { font-size: 1.7rem; }
h3 { font-size: 1.45rem; }
h4 { font-size: 1.15rem; }
a { color: #0b57d0; }
table { border-collapse: collapse; margin: 1rem 0; }
th, td { border: 1px solid #b5b5b5; padding: 0.35rem 0.7rem; text-align: left; }
th { background: #efefef; }
code { font-family: ui-monospace, monospace; background: #efefef;
       padding: 0.05rem 0.3rem; border-radius: 3px; }
.warning { border-left: 4px solid #b45309; padding-left: 0.8rem; }
@media (prefers-color-scheme: dark) {
  body { color: #e8e8e8; background: #121212; }
  a { color: #9ec1ff; }
  th { background: #232323; }
  th, td { border-color: #4a4a4a; }
  code { background: #232323; }
}
"""

CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*([^*]+)\*\*")


def _inline(text: str) -> str:
    """Escapes first, then the two spans the synthesis actually uses."""
    escaped = html.escape(text, quote=False)
    escaped = BOLD.sub(r"<strong>\1</strong>", escaped)
    return CODE.sub(r"<code>\1</code>", escaped)


def _row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _blocks(markdown: str) -> list[str]:
    """The synthesis, block by block: headings, tables, lists, paragraphs."""
    out: list[str] = []
    lines = markdown.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("#"):
            level = min(len(stripped) - len(stripped.lstrip("#")), 6)
            out.append(f"<h{level}>{_inline(stripped.lstrip('#').strip())}</h{level}>")
            i += 1
            continue
        if stripped.startswith("|"):
            header, body = _row(lines[i]), []
            i += 2  # the separator row carries no content
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append(_row(lines[i]))
                i += 1
            head = "".join(f"<th>{_inline(c)}</th>" for c in header)
            rows = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>"
                for cells in body
            )
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>")
            continue
        if stripped.startswith("- "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(f"<li>{_inline(lines[i].strip()[2:])}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        # A paragraph: consecutive plain lines, joined the way markdown joins them.
        plain = []
        while i < len(lines):
            joined = lines[i].strip()
            if not joined or joined.startswith(("#", "|", "- ")):
                break
            plain.append(joined)
            i += 1
        text = " ".join(plain)
        marker = ' class="warning"' if ":warning:" in text else ""
        out.append(f"<p{marker}>{_inline(text.replace(':warning:', '').strip())}</p>")
    return out


def synthesis_page(markdown: str, sessions: list[tuple[str, str, list[str]]] | None = None) -> str:
    """The whole page, from the synthesis text and the session pages that exist.

    `sessions` is `(label, run id, page names)` per run, **in the order to print them**.
    Ordering and labelling belong to the caller, which is the only side holding the runs;
    this renders what it is given.

    Every other section of a synthesis is organised by cell. This one used to be keyed by
    run id alone, so telling whether `1af14a46` was the baseline or the treatment meant
    opening `measures.json` - on the one page that exists to be read on its own.

    Links are relative, so the page works wherever the experiment directory is
    copied - which is the only place it is ever meant to be read from.

    A page is linked as `attempt 1`, `attempt 2`, since one file is archived per attempt.
    The file name is a timestamp and a UUID, which identifies a session and tells a reader
    nothing; it stays in `title` for whoever needs to find the file.
    """
    body = _blocks(markdown)

    if sessions:
        items = [
            f"<li>{html.escape(label)} <code>{html.escape(run_id)}</code>: "
            + " ".join(
                f'<a href="runs/{html.escape(run_id)}/session/{html.escape(name)}"'
                f' title="{html.escape(name)}">attempt {i}</a>'
                for i, name in enumerate(names, 1)
            )
            + "</li>"
            for label, run_id, names in sessions
        ]
        # `h3`, the level the synthesis heads its own sections with. As an `h2` this
        # appended section outranked every section the synthesis actually wrote.
        body.append("<h3>Sessions</h3>")
        body.append(
            "<p>The agent's own trace, one page per attempt, rendered by "
            "<code>render --html</code>.</p>"
        )
        body.append("<ul>" + "".join(items) + "</ul>")

    title = "synthesis"
    for line in markdown.split("\n"):
        if line.startswith("# "):
            title = line[2:].strip()
            break

    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n<style>\n{STYLE}</style>\n</head>\n<body>\n"
        + "\n".join(body)
        + "\n</body>\n</html>\n"
    )
