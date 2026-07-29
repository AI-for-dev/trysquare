# The mark

A try square is the instrument a joiner lays across a joint to find out whether it is
true. Turned 45 degrees, its two arms - which meet at exactly 90 degrees - read as a
check: a check that was measured rather than scribbled. The graduations on the blade
are what stop it from reading as a generic tick.

| file | colour | use |
| --- | --- | --- |
| `trysquare-mark.svg` | `currentColor` | the mark, above 32px |
| `trysquare-mark-small.svg` | `currentColor` | below 32px, where graduations close up |
| `trysquare-mark-light.svg` | `#232B33` | referenced as an image, light ground |
| `trysquare-mark-dark.svg` | `#C9D3DB` | referenced as an image, dark ground |
| `trysquare-mark-twotone.svg` | slate + brass | README, landing page |
| `trysquare-tile.svg` | slate ground | favicon, avatar, social image |
| `trysquare-lockup.svg` | slate + brass | mark and word together |
| `trysquare-square.svg` | `currentColor` | the square upright, austere variant |
| `trysquare-square-graduated.svg` | `currentColor` | the same, blade graduated |
| `trysquare-square-twotone.svg` | slate + brass | the same, in colour |
| `trysquare-verdicts.svg` | `currentColor` | status glyphs, not a logo |

**`currentColor` does not cross an `<img>` boundary.** A monochrome file inlined in a
page inherits the surrounding text colour; the same file referenced as an image does
not, and falls back to black in both themes. That is why the light and dark variants
exist as separate files rather than one file resized.

**Below 24px, remove the graduations rather than scale them** - they turn into a smear.
That is what `trysquare-mark-small.svg` is for.

The wordmark in `trysquare-lockup.svg` is live text in a monospace stack. Outline it
before publishing anywhere the fonts are not guaranteed, or it falls back silently.

Palette: slate `#232B33`, brass `#A97C2A` (`#C79338` on dark), paper `#EEF1F3`, dark
ground `#14181B`, inverse ink `#C9D3DB`. Steel and brass are the materials of the tool.
