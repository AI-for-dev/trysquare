"""Sphinx configuration.

The documentation is written in MyST Markdown, not reStructuredText: the rest of
this project's prose is Markdown, and one syntax is easier to keep good than two.

Building the docs needs Sphinx; **using trysquare does not**. These live in the
`docs` optional group and are never imported by the package.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

project = "trysquare"
author = "Loic Gouarin"
copyright = "2026, Loic Gouarin"
release = "0.1.0"

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
html_title = "trysquare"
# The brand is the lockup, so sidebar_hide_name suppresses the text furo would otherwise
# set beside it - the word is already in the drawing, and writing it twice reads as a
# mistake. html_title still names the browser tab.
#
# Two files rather than one: the lockup is two-tone, so it needs a variant per ground, and
# currentColor would not have helped anyway - it does not reach an SVG referenced as an
# image, so a monochrome file would go black in both themes.
html_favicon = "_static/logo/trysquare-tile.svg"
html_css_files = ["custom.css"]
html_theme_options = {
    "source_repository": "",
    "navigation_with_keys": True,
    "light_logo": "logo/trysquare-lockup-light.svg",
    "dark_logo": "logo/trysquare-lockup-dark.svg",
    "sidebar_hide_name": True,
}

# A warning is a defect in the documentation, and the same standard applies here as
# to the code: it fails loudly rather than accumulating quietly.
nitpicky = False
suppress_warnings: list[str] = []
