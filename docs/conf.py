"""Sphinx configuration.

The documentation is written in MyST Markdown, not reStructuredText: the rest of
this project's prose is Markdown, and one syntax is easier to keep good than two.

Building the docs needs Sphinx; **using trysquare does not**. These live in the
`docs` optional group and are never imported by the package.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# This directory too, so `pygments_style` below can name `_pygments` by module path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from trysquare import __version__  # noqa: E402 - the path above is what makes it importable

project = "trysquare"
# The holders LICENSE names, so a page and a licence cannot disagree about who they are.
copyright = "2026, The trysquare Authors"
author = "Loic Gouarin"
# Derived rather than written a third time: `pyproject.toml` names the version.
release = __version__

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_copybutton",
    "sphinx_design",
]

myst_enable_extensions = [
    "colon_fence",  # ::: fences, so admonitions read as Markdown
    "deflist",  # definition lists for the reference tables
    "fieldlist",
    "linkify",  # bare URLs become links
    "substitution",
    "tasklist",
]
myst_heading_anchors = 3

# The reference pages are generated from docstrings, and the docstrings in this
# project carry the reasoning behind each rule. Keeping them in source order rather
# than alphabetical preserves that argument.
autodoc_member_order = "bysource"
autodoc_typehints = "description"
napoleon_google_docstring = True
napoleon_numpy_docstring = False

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}

templates_path = ["_templates"]
# README.md documents how to *build* these docs, for someone browsing the repository.
# It is not a page of the documentation, so Sphinx must not collect it - otherwise it
# is an orphan in every toctree, and with -W that is a build failure.
exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
    "README.md",
    "requirements.txt",
    # _static is copied verbatim, not parsed. Its own README.md documents the marks for
    # whoever reuses them, and Sphinx would otherwise collect it as a document with no
    # place in any toctree - an orphan, and with -W an orphan is a failed build.
    "_static/**",
]

html_theme = "furo"
html_static_path = ["_static"]
# Furo derives the code background, the gutter and the copy button from the pygments style
# itself, so the blocks join the palette only if the style does. `_pygments.py` says why.
pygments_style = "_pygments.Slate"
pygments_dark_style = "_pygments.SlateDark"
html_title = "trysquare"
# The brand is the lockup, so sidebar_hide_name suppresses the text furo would otherwise
# set beside it - the word is already in the drawing, and writing it twice reads as a
# mistake. html_title still names the browser tab.
#
# Two files rather than one: the lockup is two-tone, so it needs a variant per ground, and
# currentColor would not have helped anyway - it does not reach an SVG referenced as an
# image, so a monochrome file would go black in both themes.
html_favicon = "_static/logo/trysquare-tile.svg"
# cheatsheet.css is loaded on every page and inert on all but one: every selector in
# it is under `.ts-sheet`, which only the cheat sheet carries.
html_css_files = ["custom.css", "cheatsheet.css"]

# Furo's variables, pointed at the palette `_static/custom.css` declares. The map lives
# here rather than in the stylesheet because furo emits it in an inline <style> after every
# linked sheet, on `body[data-theme="dark"]` - a rule in custom.css on plain `body` has
# lower specificity and would lose in dark mode.
#
# One dict handed to both keys, for that same reason: the values point at `--ts-*` tokens,
# and it is those tokens that flip. But furo declares its *own* dark values on the more
# specific selector, so the map has to be repeated there to outrank them rather than
# quietly inherited from the light one.
_palette = {
    "font-stack": "var(--ts-serif)",
    "font-stack--monospace": "var(--ts-mono)",
    # The headings are mono, as the wordmark is: what the tool says of itself is set in its
    # own letters, and what is said about it is set in the serif.
    "font-stack--headings": "var(--ts-mono)",
    # Ink and ground. The content is paper and the chrome is the ground - the same relation
    # the cheat sheet has between its cards and what they sit on, and already how furo
    # splits primary from secondary. Reversed, twelve pages of running prose become a grey
    # slab.
    "color-foreground-primary": "var(--ts-ink)",
    "color-foreground-secondary": "var(--ts-ink-2)",
    "color-foreground-muted": "var(--ts-ink-3)",
    "color-foreground-border": "var(--ts-rule)",
    "color-background-primary": "var(--ts-panel)",
    "color-background-secondary": "var(--ts-ground)",
    "color-background-hover": "var(--ts-panel-2)",
    "color-background-hover--transparent": "var(--ts-panel-2-clear)",
    "color-background-border": "var(--ts-rule-soft)",
    "color-background-item": "var(--ts-ink-3)",
    # Brass carries links and marks. `--ts-brass-ink` wherever the brass is *text*:
    # #A97C2A on paper is 3.75:1, and text needs 4.5.
    "color-brand-primary": "var(--ts-brass)",
    "color-brand-content": "var(--ts-brass-ink)",
    "color-brand-visited": "var(--ts-brass-ink)",
    "color-link-underline": "var(--ts-rule)",
    "color-link-underline--hover": "var(--ts-brass)",
    "color-link-underline--visited": "var(--ts-rule)",
    "color-link-underline--visited--hover": "var(--ts-brass)",
    "color-highlight-on-target": "var(--ts-brass-wash)",
    "color-highlighted-background": "var(--ts-brass-wash)",
    "color-inline-code-background": "var(--ts-panel-2)",
    "color-sidebar-background": "var(--ts-ground)",
    "color-sidebar-background-border": "var(--ts-rule)",
    "color-sidebar-caption-text": "var(--ts-ink-2)",
    "color-sidebar-link-text": "var(--ts-ink-2)",
    # Not the brand colour: furo paints every top-level entry in it, and thirteen brass
    # links stacked in a column spend the accent on what is *not* the answer. Brass appears
    # once in the sidebar, on the page you are reading.
    "color-sidebar-link-text--top-level": "var(--ts-ink)",
    # Transparent, because the default is the secondary background - which after this map
    # is the sidebar's own colour, so the field would vanish into it. Furo rules the input
    # top and bottom; the ground behind it and a paper fill on focus are field enough.
    "color-sidebar-search-background": "transparent",
    "color-sidebar-search-background--focus": "var(--ts-panel)",
    "color-sidebar-search-border": "var(--ts-rule)",
    "color-sidebar-search-icon": "var(--ts-ink-3)",
    # `-foreground` and not `-text`: furo declares `--color-sidebar-search-text`, but the
    # rule that colours the input reads `--color-sidebar-search-foreground`. Setting the
    # declared name does nothing at all.
    "color-sidebar-search-foreground": "var(--ts-ink)",
    "color-toc-title-text": "var(--ts-ink-2)",
    "color-toc-item-text": "var(--ts-ink-2)",
    "color-toc-item-text--hover": "var(--ts-ink)",
    "color-toc-item-text--active": "var(--ts-brass-ink)",
    # An admonition is a rule and a title strip, as the cheat sheet's note is. Furo fills
    # the body - transparent in light, #18181a in dark - and neither is in this palette.
    # The *title* colours are furo's own and stay: they are semantic, and a brass "danger"
    # would be decoration pretending to be meaning.
    "color-admonition-background": "transparent",
    "color-table-border": "var(--ts-rule-soft)",
    "color-table-header-background": "transparent",
    # The API page. `color-api-name` defaults to `--color-problematic`, so every function
    # name on it is red today.
    "color-api-name": "var(--ts-ink)",
    "color-api-pre-name": "var(--ts-ink-3)",
    "color-api-paren": "var(--ts-ink-3)",
    "color-api-keyword": "var(--ts-brass-ink)",
    "color-api-overall": "var(--ts-ink-2)",
    "color-api-background": "var(--ts-panel-2-clear)",
    "color-api-background-hover": "var(--ts-panel-2)",
    # sphinx-design's own tokens, which furo-extensions declares on `body` as well.
    "sd-color-card-background": "var(--ts-panel)",
    "sd-color-card-border": "var(--ts-rule-soft)",
    "sd-color-card-border-hover": "var(--ts-brass)",
    "sd-color-card-text": "var(--ts-ink)",
    "sd-color-card-header": "var(--ts-panel-2)",
    "sd-color-card-footer": "var(--ts-panel-2)",
    "sd-color-shadow": "rgba(35, 43, 51, 0.08)",
    "sd-color-primary": "var(--ts-brass)",
    "sd-color-primary-text": "var(--ts-panel)",
    "sd-color-primary-highlight": "var(--ts-brass-ink)",
}
# `--color-problematic` and the `--color-api-added/changed/deprecated/removed` pairs are
# left alone on purpose. They are semantic, they are rare, and a brass "removed" would be
# decoration pretending to be meaning.

html_theme_options = {
    "source_repository": "",
    "navigation_with_keys": True,
    "light_logo": "logo/trysquare-lockup-light.svg",
    "dark_logo": "logo/trysquare-lockup-dark.svg",
    "sidebar_hide_name": True,
    "light_css_variables": _palette,
    "dark_css_variables": _palette,
}

# A warning is a defect in the documentation, and the same standard applies here as
# to the code: it fails loudly rather than accumulating quietly.
nitpicky = False
suppress_warnings: list[str] = []
