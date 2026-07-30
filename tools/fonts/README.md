# JetBrains Mono, vendored

The typeface the wordmark is outlined from, committed so that
`tools/build-lockup.py` regenerates the same lockup on any machine. A generator that
depends on whatever font happens to be installed is a generator that silently produces a
different wordmark.

| | |
| --- | --- |
| version | 2.304 (`ttfautohint v1.8.4.7-5d5b`) |
| sha256 | `a0bf60ef0f83c5ed4d7a75d45838548b1f6873372dfac88f71804491898d138f` |
| source | <https://github.com/JetBrains/JetBrainsMono> |
| licence | SIL Open Font License 1.1, `OFL.txt` |

Unmodified: 1743 glyphs, name table untouched. The file was taken from two independent
copies on the build machine, a system font package and an application bundle, and the two
were byte-identical - which is the only evidence available offline that it is the upstream
artifact rather than someone's subset.

`build-lockup.py` checks that digest before it reads a single glyph, and refuses rather
than emitting a lockup in the wrong typeface. A wrong wordmark does not look wrong, it
just looks like a wordmark.

## What the licence requires

The OFL permits redistribution, embedding, and outlining glyphs into a derivative work.
It requires that the copyright notice and licence travel with the font, which is what
`OFL.txt` and `AUTHORS.txt` are doing here. Do not delete them to tidy the directory.

The copyright line declares **no Reserved Font Name**, so even a modified or subset
version could keep the name JetBrains Mono. This copy is unmodified, so the question does
not arise.

The faces macOS ships - Menlo, SF Mono, Courier - are licensed by Apple and grant no such
permission. That is why the wordmark is not set in the font the rest of this project's
documentation is read in.
