"""Syntax colouring in the same two hues as the rest of the page.

Furo ships `a11y-light` and `native`, writes its own `pygments.css` at build time, and -
this is the part that matters - derives `--color-code-background` and
`--color-code-foreground` from `style.background_color` and the colour it resolves for
`Text`. Those two drive the block background, the line-number gutter, the code-block
caption and the copy button, so overriding token classes in the stylesheet would fight a
generated file and still leave four things the wrong colour. A style class settles all of
it in one place.

The scheme has three roles, taken from the page this design comes from: a literal is
brass, a comment recedes, a keyword is the ink at full weight. Punctuation drops to the
secondary ink so structure reads without being coloured, and nothing else is coloured at
all. Eight hues in a code block is decoration that reads as meaning; the page already says
what matters by where it puts it.

No italic comment. There is no mono italic in the subset - see tools/build-webfonts.py -
and a synthesised oblique in a code block is the one place a slant looks like a rendering
fault rather than a choice.

The colours are the palette in `_static/custom.css`, written out as literals because
Pygments emits a stylesheet, not custom properties, and cannot read a `var()`.
"""

from __future__ import annotations

from pygments.style import Style
from pygments.token import (
    Comment,
    Error,
    Generic,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Text,
)

# Light: ink #232B33, ink-2 #5D6873, ink-3 #7E8894, panel-2 #E4E9ED, brass #A97C2A.
# Dark:  ink #C9D3DB, ink-2 #93A0AC, ink-3 #74808C, panel-2 #232A30, brass #C79338.


class Slate(Style):
    """The light scheme."""

    background_color = "#E4E9ED"
    highlight_color = "#F3E9D6"
    line_number_color = "#7E8894"
    line_number_background_color = "#E4E9ED"

    styles = {  # noqa: RUF012 - Pygments reads this as a plain class attribute
        Text: "#232B33",
        Comment: "#7E8894",
        Comment.Preproc: "#5D6873",
        Keyword: "bold #232B33",
        Keyword.Constant: "#A97C2A",
        Name: "#232B33",
        Name.Function: "bold #232B33",
        Name.Class: "bold #232B33",
        Name.Tag: "bold #232B33",
        Name.Attribute: "#5D6873",
        Name.Decorator: "#A97C2A",
        String: "#A97C2A",
        String.Escape: "bold #A97C2A",
        Number: "#A97C2A",
        Operator: "#5D6873",
        Punctuation: "#5D6873",
        Generic.Prompt: "#7E8894",
        Generic.Output: "#5D6873",
        Generic.Emph: "italic",
        Generic.Strong: "bold",
        Generic.Heading: "bold #232B33",
        Error: "#B30000",
    }


class SlateDark(Style):
    """The dark scheme, one step lighter throughout and with the brass lifted."""

    background_color = "#232A30"
    highlight_color = "#2A2419"
    line_number_color = "#74808C"
    line_number_background_color = "#232A30"

    styles = {  # noqa: RUF012 - Pygments reads this as a plain class attribute
        Text: "#C9D3DB",
        Comment: "#74808C",
        Comment.Preproc: "#93A0AC",
        Keyword: "bold #C9D3DB",
        Keyword.Constant: "#C79338",
        Name: "#C9D3DB",
        Name.Function: "bold #C9D3DB",
        Name.Class: "bold #C9D3DB",
        Name.Tag: "bold #C9D3DB",
        Name.Attribute: "#93A0AC",
        Name.Decorator: "#C79338",
        String: "#C79338",
        String.Escape: "bold #C79338",
        Number: "#C79338",
        Operator: "#93A0AC",
        Punctuation: "#93A0AC",
        Generic.Prompt: "#74808C",
        Generic.Output: "#93A0AC",
        Generic.Emph: "italic",
        Generic.Strong: "bold",
        Generic.Heading: "bold #C9D3DB",
        Error: "#FF7575",
    }
