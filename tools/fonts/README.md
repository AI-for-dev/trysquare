# The typefaces, vendored

Two families, committed so that `tools/build-lockup.py` and `tools/build-webfonts.py`
produce the same artifact on any machine. A generator that depends on whatever font
happens to be installed is a generator that silently produces something different.

The documentation is read in both: **JetBrains Mono** for everything the tool says of
itself - headings, code, flags, the chrome - and **Source Serif 4** for everything said
*about* it. The mono is the same face the wordmark is outlined from, so the brand in the
sidebar and the prose beside it are finally the same letters.

## JetBrains Mono

| | |
| --- | --- |
| version | 2.304 (`ttfautohint v1.8.4.7-5d5b`) |
| files | `JetBrainsMono-Regular.ttf`, `JetBrainsMono-Bold.ttf` |
| sha256 | `a0bf60ef…898d138f`, `5590990c…42479dcb` |
| source | <https://github.com/JetBrains/JetBrainsMono> |
| licence | SIL Open Font License 1.1, `OFL.txt`, `AUTHORS.txt` |

Unmodified, name tables untouched. The Regular was taken from two independent copies on
the build machine - a system font package and an application bundle - and later from the
upstream `JetBrainsMono-2.304.zip` release, and all three were byte-identical. That is
the strongest evidence available that it is the upstream artifact and not someone's
subset. The Bold comes from that same release archive.

## Source Serif 4

| | |
| --- | --- |
| version | 4.005 (`hotconv 1.1.0`, `makeotfexe 2.6.0`) |
| files | `SourceSerif4-{Regular,It,Semibold,SemiboldIt}.ttf` |
| sha256 | `e5a4ee6a…1668e24b`, `9d2950a8…32e7f430`, `36db6294…5fc4655f`, `d6a0a431…6f3c9899` |
| source | <https://github.com/adobe-fonts/source-serif>, `TTF/` on the `release` branch |
| licence | SIL Open Font License 1.1, `SourceSerif4-LICENSE.md` |

Unmodified as vendored here. Four faces and not two: the prose leans on `*emphasis*`
constantly, and a roman sheared into an oblique is visibly not an italic. Semibold rather
than Bold because `strong` asks for 700, finds 400 and 600 declared, and settles on 600
without synthesising anything.

Both build scripts check every digest before reading a glyph, and refuse rather than
emitting the wrong thing. A wrong typeface does not look wrong; it just looks like a
typeface.

## What the licence requires

The OFL permits redistribution, embedding, subsetting, and outlining glyphs into a
derivative work. It requires that the copyright notice and licence travel with the font,
which is what `OFL.txt`, `AUTHORS.txt` and `SourceSerif4-LICENSE.md` are doing here, and
why they are copied beside the webfonts in `docs/_static/fonts/` as well. Do not delete
them to tidy either directory.

**The two families answer the Reserved Font Name question differently, and it changes
what we may ship.**

JetBrains Mono's copyright line declares no reserved name, so its Latin subset keeps the
name JetBrains Mono.

Adobe's declares one - *"Copyright 2014 - 2023 Adobe (http://www.adobe.com/), with
Reserved Font Name 'Source'."* The OFL FAQ (2.6-2.8) allows a **modified** version to
keep a reserved name only where it is *functionally equivalent* to the original, which
requires the same character inventory. Subsetting to Latin drops the Greek and the
Cyrillic, so the webfont is not functionally equivalent and may not be called Source
Serif. `build-webfonts.py` therefore renames it **Trysquare Serif** in the name table.

What moves is only what *identifies* the font: family, subfamily, unique ID, full name,
PostScript name, and the typographic pair. The copyright, the licence, the licence URL,
the designer and Adobe's trademark notice are left exactly as written - clause 1 requires
the notice to travel, and the rename is the thing that makes honouring it matter.

The TTFs vendored here are untouched, so the name in this directory is still Source
Serif 4. The rename happens on the way out.

The faces macOS ships - Menlo, SF Mono, Courier - are licensed by Apple and grant none of
this. That is why neither the wordmark nor the documentation is set in them.
