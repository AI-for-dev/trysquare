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
    def test_links_are_relative_and_named(self):
        page = synthesis_page(SYNTHESIS, {"abcd1234": ["one.html", "two.html"]})
        assert '<a href="runs/abcd1234/session/one.html">one.html</a>' in page
        assert '<a href="runs/abcd1234/session/two.html">two.html</a>' in page

    def test_no_archived_page_means_no_section(self):
        assert "<h2>Sessions</h2>" not in synthesis_page(SYNTHESIS, {})
