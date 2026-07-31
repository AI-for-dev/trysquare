"""Cut the vendored fonts down to the webfonts the documentation is served in.

    uv run --with "fonttools[woff]" python tools/build-webfonts.py

`[woff]` and not bare `fonttools`: WOFF2 needs brotli, which the bare install does not
pull. Nothing here enters the `docs` extra - the woff2 are committed, so building the
documentation never sees fonttools, exactly as it never sees it for the lockup.

The documentation is read in the same two faces the mark is drawn from: JetBrains Mono
for everything the tool says of itself, and a serif for everything said *about* it. Both
are under the SIL Open Font License; see tools/fonts/README.md for where each came from
and what its licence asks of us.

Each source digest is checked before a glyph is read. A generator that takes whatever
font is installed produces a page that is wrong without looking wrong.

Two things differ per family, and both are decisions rather than defaults:

**The mono carries the box-drawing block, the serif does not.** Fourteen pages draw
tables and trees out of `─ │ ├ └`, and `unicode-range` decides what is *fetched*: a
codepoint outside the declared range falls back to another face even when the glyph is
right there, which breaks a table into mixed advance widths. So the range and the subset
are cut to the same list, and the list is written once, below. Source Serif has no box
glyphs at all, so declaring them for it would only invite a fallback.

**The mono drops its ligatures.** JetBrains Mono draws `->` as a single arrow through
`calt`, and a command a reader retypes from a code block must be the command. The serif
keeps the subsetter's defaults, because kerning is not optional in a text face.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "fonts"
OUT = HERE.parent / "docs" / "_static" / "fonts"

# The `latin` range Google Fonts cuts to, which is what the artwork this system comes
# from was built against - with the arrow row widened from the two it declared to all
# four, because Sphinx writes a `->` as U+2192 in every return annotation on the API
# page.
LATIN: tuple[tuple[int, int], ...] = (
    (0x0000, 0x00FF),
    (0x0131, 0x0131),
    (0x0152, 0x0153),
    (0x02BB, 0x02BC),
    (0x02C6, 0x02C6),
    (0x02DA, 0x02DA),
    (0x02DC, 0x02DC),
    (0x0304, 0x0304),
    (0x0308, 0x0308),
    (0x0329, 0x0329),
    (0x2000, 0x206F),
    (0x20AC, 0x20AC),
    (0x2122, 0x2122),
    (0x2190, 0x2193),
    (0x2212, 0x2212),
    (0x2215, 0x2215),
    (0xFEFF, 0xFEFF),
    (0xFFFD, 0xFFFD),
)

# Box Drawing and Block Elements, whole. The pages use nine of these 160 today, but a
# table drawn next year should not have to come back here first, and the whole block
# costs about a kilobyte compressed.
BOX: tuple[tuple[int, int], ...] = ((0x2500, 0x259F),)

# Ligatures and contextual alternates are what turn `->` into an arrow; ccmp, mark and
# mkmk are composition and diacritic placement, which are not decoration.
MONO_FEATURES = ["ccmp", "locl", "mark", "mkmk", "kern"]


@dataclass(frozen=True)
class Face:
    """One source file, and the one webfont cut from it."""

    source: str
    sha256: str
    out: str
    ranges: tuple[tuple[int, int], ...]
    weight: int
    italic: bool
    features: list[str] | None = None
    # Set only where the licence forces a new name; see `rename` and tools/fonts/README.md.
    family: str | None = None
    style: str | None = None
    postscript: str | None = None


FACES: tuple[Face, ...] = (
    Face(
        source="JetBrainsMono-Regular.ttf",
        sha256="a0bf60ef0f83c5ed4d7a75d45838548b1f6873372dfac88f71804491898d138f",
        out="jetbrains-mono-400.woff2",
        ranges=LATIN + BOX,
        weight=400,
        italic=False,
        features=MONO_FEATURES,
    ),
    Face(
        source="JetBrainsMono-Bold.ttf",
        sha256="5590990c82e097397517f275f430af4546e1c45cff408bde4255dad142479dcb",
        out="jetbrains-mono-700.woff2",
        ranges=LATIN + BOX,
        weight=700,
        italic=False,
        features=MONO_FEATURES,
    ),
    Face(
        source="SourceSerif4-Regular.ttf",
        sha256="e5a4ee6a3d87bb9024796be390c6771e2a0eb1883dae25effaf57ca01668e24b",
        out="trysquare-serif-400.woff2",
        ranges=LATIN,
        weight=400,
        italic=False,
        family="Trysquare Serif",
        style="Regular",
        postscript="TrysquareSerif-Regular",
    ),
    Face(
        source="SourceSerif4-It.ttf",
        sha256="9d2950a8f1da66e21502c35d646a1d2148e79f9ea43fd2158cf02f5232e7f430",
        out="trysquare-serif-400-italic.woff2",
        ranges=LATIN,
        weight=400,
        italic=True,
        family="Trysquare Serif",
        style="Italic",
        postscript="TrysquareSerif-It",
    ),
    Face(
        source="SourceSerif4-Semibold.ttf",
        sha256="36db62940cb5728b12b1802476dc7fcf4c6c519a7bdd476ba23a4e555fc4655f",
        out="trysquare-serif-600.woff2",
        ranges=LATIN,
        weight=600,
        italic=False,
        family="Trysquare Serif",
        style="Semibold",
        postscript="TrysquareSerif-Semibold",
    ),
    Face(
        source="SourceSerif4-SemiboldIt.ttf",
        sha256="d6a0a4317102a255a55850640332ba3acc7c872606a555516b4ecdb66f3c9899",
        out="trysquare-serif-600-italic.woff2",
        ranges=LATIN,
        weight=600,
        italic=True,
        family="Trysquare Serif",
        style="Semibold Italic",
        postscript="TrysquareSerif-SemiboldIt",
    ),
)

# Rewritten when a face is renamed. Everything else in the name table stays, and 0, 13
# and 14 - copyright, licence, licence URL - stay *because* of the rename: clause 1 of
# the OFL requires the notice to travel with the font.
FAMILY_ID, SUBFAMILY_ID, UNIQUE_ID, FULL_ID = 1, 2, 3, 4
POSTSCRIPT_ID = 6
TYPO_FAMILY_ID, TYPO_SUBFAMILY_ID = 16, 17


def codepoints(ranges: tuple[tuple[int, int], ...]) -> set[int]:
    return {c for start, end in ranges for c in range(start, end + 1)}


def css_range(ranges: tuple[tuple[int, int], ...]) -> str:
    """The same list as the subset, in the form a stylesheet wants it."""
    return ", ".join(
        f"U+{start:04X}" if start == end else f"U+{start:04X}-{end:04X}"
        for start, end in sorted(ranges)
    )


def rename(font: TTFont, face: Face) -> None:
    """Give the subset a name of its own.

    Adobe reserves the name 'Source'. The OFL lets a modified version keep a reserved
    name only if it is functionally equivalent (FAQ 2.6-2.8), and a Latin subset is not:
    the Greek and the Cyrillic go. So the subset is named for this project instead.

    Only the names that *identify* the font move. The copyright, the licence, the
    designer and the trademark notice are left exactly as Adobe wrote them.
    """
    # A weight outside Regular and Bold cannot be addressed by the four-way legacy pair,
    # so it moves into the family name and the pair carries the slope alone. IDs 16 and
    # 17 then hold the real family and the real style, which is what a modern shaper reads.
    weight_name = (
        "Regular" if face.style in ("Regular", "Italic") else face.style.removesuffix(" Italic")
    )
    ribbi = weight_name in ("Regular", "Bold")
    legacy_family = face.family if ribbi else f"{face.family} {weight_name}"
    legacy_style = face.style if ribbi else ("Italic" if face.italic else "Regular")

    replacements = {
        FAMILY_ID: legacy_family,
        SUBFAMILY_ID: legacy_style,
        UNIQUE_ID: face.postscript,
        FULL_ID: f"{face.family} {face.style}",
        POSTSCRIPT_ID: face.postscript,
        TYPO_FAMILY_ID: face.family,
        TYPO_SUBFAMILY_ID: face.style,
    }
    for record in font["name"].names:
        if record.nameID in replacements:
            record.string = replacements[record.nameID]


def build(face: Face) -> int:
    source = SOURCES / face.source
    if not source.exists():
        raise SystemExit(f"font not found: {source}")

    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != face.sha256:
        raise SystemExit(
            f"{face.source} is not the font this webfont was cut from.\n"
            f"  expected {face.sha256}\n"
            f"  found    {digest}\n"
            "Restore it, or update the digest and tools/fonts/README.md deliberately."
        )

    font = TTFont(source, recalcTimestamp=False)

    options = Options()
    options.flavor = "woff2"
    # The subsetter's default keeps seven name records and drops the rest, which would
    # take the licence with it. Keep the whole table; it is under a kilobyte.
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.name_legacy = True
    if face.features is not None:
        options.layout_features = face.features

    subsetter = Subsetter(options=options)
    subsetter.populate(unicodes=codepoints(face.ranges))
    subsetter.subset(font)

    if face.family:
        rename(font, face)

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / face.out
    font.flavor = "woff2"
    font.save(target)
    font.close()
    return target.stat().st_size


def main() -> None:
    total = 0
    for face in FACES:
        size = build(face)
        total += size
        print(f"{face.out:34s} {size / 1024:6.1f} KB   {len(codepoints(face.ranges)):5d} cp")
    print(f"{'':34s} {total / 1024:6.1f} KB total")

    # Printed rather than kept in the stylesheet by hand: the range that is declared and
    # the range that was cut have to be the same list, and two copies of a list drift.
    print(f"\nunicode-range, serif:\n  {css_range(LATIN)}")
    print(f"\nunicode-range, mono:\n  {css_range(LATIN + BOX)}")


if __name__ == "__main__":
    main()
