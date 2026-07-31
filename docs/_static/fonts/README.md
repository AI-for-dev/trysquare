# The webfonts

Generated, not hand-made. Regenerate them rather than editing a `.woff2`:

```bash
uv run --with "fonttools[woff]" python tools/build-webfonts.py
```

That script holds the sources, their digests, the ranges each face is cut to, and the
rename the licence requires. `tools/fonts/README.md` says where the sources came from.

| file | family | weight | 
| --- | --- | --- |
| `jetbrains-mono-400.woff2` | JetBrains Mono | 400 |
| `jetbrains-mono-700.woff2` | JetBrains Mono | 700 |
| `trysquare-serif-400.woff2` | Trysquare Serif | 400 |
| `trysquare-serif-400-italic.woff2` | Trysquare Serif | 400 italic |
| `trysquare-serif-600.woff2` | Trysquare Serif | 600 |
| `trysquare-serif-600-italic.woff2` | Trysquare Serif | 600 italic |

About 19 KB each, 114 KB for the six; a prose page pulls five of them.

**Trysquare Serif is Source Serif 4, subset and renamed.** Adobe reserves the name
'Source', and the OFL only lets a modified version keep a reserved name when it is
functionally equivalent - which a Latin subset is not. The reasoning is written out in
`tools/fonts/README.md`; the copyright and licence records inside each file are Adobe's,
untouched.

`OFL.txt` and `AUTHORS.txt` are JetBrains Mono's; `SourceSerif4-LICENSE.md` is Adobe's.
They sit here rather than only in `tools/fonts/` because `html_static_path` copies this
directory verbatim into the built site, so the notices are served alongside the fonts
they cover, which is what the licence asks of a redistribution. Do not delete them.

**Committed on purpose.** Building the documentation must not need fonttools - the same
rule the outlined lockup follows. `make -C docs html` and CI never see it.

**The mono carries the box-drawing block and the serif does not.** Fourteen pages draw
tables and trees out of `─ │ ├ └`, and `unicode-range` decides what gets *fetched*: a
codepoint outside the declared range falls back to another face even when the glyph is
present, which breaks a table into mixed advance widths. The declared range and the
subset are cut from one list in the script, so the two cannot drift.
