"""The synthesis as a page: self-contained, offline, and faithful to the markdown.

The converter in `pages.py` is deliberately narrow - it reads the shapes
`table.py` emits and nothing more general - so these tests are the contract that
keeps the two from drifting apart.
"""

from trysquare.pages import synthesis_page

SYNTHESIS = """\
# A title with `code` in it

- etalon `etalon-v1`, provider `p`, model `m`, thinking `off`

### Scores, cell by test

| cell | in_scope |
| --- | --- |
| nothing / off | 7/10 |
| rule / off | 9/10 |

`x/n`: the test was true in `x` of the `n` runs that could judge it.

**No sentence may rest on an `o`.** The table shows them anyway:
hiding a measurement would be another dishonesty.

:warning: **The cost columns must not be read here.** 3 retries across the matrix.
"""


class TestThePage:
    def test_the_page_is_self_contained(self):
        """No script, no external asset: an archive must render offline in five years."""
        page = synthesis_page(SYNTHESIS)
        assert page.startswith("<!doctype html>")
        assert "<script" not in page
        assert "http://" not in page and "https://" not in page
        assert "@import" not in page and "url(" not in page

    def test_the_shapes_the_synthesis_uses_all_render(self):
        page = synthesis_page(SYNTHESIS)
        assert "<h1>A title with <code>code</code> in it</h1>" in page
        assert "<th>in_scope</th>" in page
        assert "<td>7/10</td>" in page and "<td>9/10</td>" in page
        assert "<li>etalon <code>etalon-v1</code>" in page
        assert "<strong>No sentence may rest on an <code>o</code>.</strong>" in page

    def test_two_plain_lines_are_one_paragraph(self):
        page = synthesis_page(SYNTHESIS)
        assert "anyway: hiding a measurement" in page

    def test_the_retry_warning_keeps_its_weight(self):
        page = synthesis_page(SYNTHESIS)
        assert '<p class="warning">' in page
        assert ":warning:" not in page

    def test_markup_in_a_cell_name_is_text_not_html(self):
        """A cell named `<b>` must never become an element of the page."""
        page = synthesis_page("| cell |\n| --- |\n| a <b> name & co |\n")
        assert "<td>a &lt;b&gt; name &amp; co</td>" in page

    def test_the_title_comes_from_the_first_heading(self):
        assert "<title>A title with `code` in it</title>" in synthesis_page(SYNTHESIS)


class TestSessionLinks:
    def test_links_are_relative_and_numbered_by_attempt(self):
        """One file is archived per attempt, so the ordinal is what a reader wants. The
        name is a timestamp and a UUID: it identifies the session and says nothing, so it
        stays in `title` for whoever has to find the file."""
        page = synthesis_page(
            SYNTHESIS, [("rule #0", "abcd1234", "runs/rule/abcd1234", ["one.html", "two.html"])]
        )
        assert (
            '<a href="runs/rule/abcd1234/session/one.html" title="one.html">attempt 1</a>' in page
        )
        assert (
            '<a href="runs/rule/abcd1234/session/two.html" title="two.html">attempt 2</a>' in page
        )

    def test_each_run_is_named_by_its_cell(self):
        """Every other section of a synthesis is organised by cell. Keyed by run id alone,
        this was the one place where telling the baseline from the treatment meant opening
        measures.json."""
        page = synthesis_page(SYNTHESIS, [("rule #0", "abcd1234", "runs/abcd1234", ["one.html"])])
        assert "rule #0 <code>abcd1234</code>" in page

    def test_the_caller_s_order_is_kept(self):
        """Ordering belongs to the side holding the runs, so this must not re-sort: by run
        id, `rule` would come before `nothing` and contradict the tables above it."""
        page = synthesis_page(
            SYNTHESIS,
            [
                ("nothing #0", "ffff0000", "runs/nothing/ffff0000", ["a.html"]),
                ("rule #0", "0000ffff", "runs/rule/0000ffff", ["b.html"]),
            ],
        )
        assert page.index("nothing #0") < page.index("rule #0")

    def test_no_archived_page_means_no_section(self):
        assert "Sessions</h3>" not in synthesis_page(SYNTHESIS, [])

    def test_the_appended_section_sits_at_the_level_the_synthesis_writes(self):
        """The synthesis heads every section it writes with `###`. As an `h2` the one
        section nobody measured led the page."""
        page = synthesis_page(SYNTHESIS, [("rule #0", "abcd1234", "runs/abcd1234", ["one.html"])])
        assert "<h3>Sessions</h3>" in page


class TestTheArchiveIsReadable:
    """This page outlives everything else in the tree, so how it reads is not a detail:
    somebody opening it in five years has its own style block and nothing else."""

    def test_every_heading_level_is_told_its_size(self):
        """A synthesis heads sections with `###` and subsections with `####`. On browser
        defaults that is 18.7px and 16px - the second being exactly body text, and both
        being what a table header already looks like."""
        page = synthesis_page(SYNTHESIS)
        for level in ("h1", "h2", "h3", "h4"):
            assert f"{level} {{ font-size:" in page, level

    def test_links_are_told_their_colour_in_both_themes(self):
        """Unstyled, a link is `#0000EE`: 1.99:1 against the dark background, where 4.5
        is the floor for legibility. The session links are the only things to click."""
        light, dark = synthesis_page(SYNTHESIS).split("prefers-color-scheme: dark")
        assert "a { color:" in light
        assert "a { color:" in dark
