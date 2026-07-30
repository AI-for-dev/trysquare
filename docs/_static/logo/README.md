# The mark

A try square is the instrument a joiner lays across a joint to find out whether it is
true. Turned 45 degrees, its two arms - which meet at exactly 90 degrees - read as a
check: a check that was measured rather than scribbled. Two things keep it from being a
generic tick. The brass stock gives the check a handle, so the eye sees a tool being
held. The graduations on the blade say it measures.

The mark is **the graduated two-tone**: `trysquare-mark-light.svg` on a light ground,
`trysquare-mark-dark.svg` on a dark one. Everything else in this directory serves a
constraint that one cannot.

| file | colour | use |
| --- | --- | --- |
| `trysquare-mark-light.svg` | slate + brass | **the mark**, light ground, above 32px |
| `trysquare-mark-dark.svg` | paper + brass | **the mark**, dark ground, above 32px |
| `trysquare-mark-small-light.svg` | slate + brass | below 32px, light ground |
| `trysquare-mark-small-dark.svg` | paper + brass | below 32px, dark ground |
| `trysquare-mark.svg` | `currentColor` | inlined in a page, follows the text colour |
| `trysquare-mark-small.svg` | `currentColor` | the same, below 32px |
| `trysquare-tile.svg` | slate ground | favicon, avatar, social image |
| `trysquare-lockup.svg` | slate + brass | mark and word together |
| `trysquare-square.svg` | `currentColor` | the square upright, austere variant |
| `trysquare-square-graduated.svg` | `currentColor` | the same, blade graduated |
| `trysquare-square-twotone.svg` | slate + brass | the same, in colour |
| `trysquare-verdicts.svg` | `currentColor` | status glyphs, not a logo |

**Two tones need two grounds.** Slate `#232B33` disappears on `#14181B`, so the dark
variant inverts the blade to the paper ink and lifts the brass one step to `#C79338`,
which would otherwise go muddy. The graduations are cut out of the blade rather than
drawn on it, so they show whichever ground is behind them and cost nothing to invert.

**`currentColor` does not cross an `<img>` boundary.** A monochrome file inlined in a
page inherits the surrounding text colour; the same file referenced as an image does
not, and falls back to black in both themes. That is why `trysquare-mark.svg` is for
inlining only, and why anything referenced by `src` or `href` picks the light or dark
file explicitly.

**Below 24px, remove the graduations rather than scale them** - they turn into a smear.
That is what `trysquare-mark-small.svg` is for.

The wordmark in `trysquare-lockup.svg` is live text in a monospace stack. Outline it
before publishing anywhere the fonts are not guaranteed, or it falls back silently.

Palette: slate `#232B33`, brass `#A97C2A` (`#C79338` on dark), paper `#EEF1F3`, dark
ground `#14181B`, inverse ink `#C9D3DB`. Steel and brass are the materials of the tool.
