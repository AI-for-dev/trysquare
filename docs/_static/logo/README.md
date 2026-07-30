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
| `trysquare-lockup-light.svg` | slate + brass | mark and word, light ground |
| `trysquare-lockup-dark.svg` | paper + brass | mark and word, dark ground |
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

**The lockup carries no font.** Its wordmark is outlined, so it cannot fall back silently
on a reader who lacks the typeface. Regenerate it rather than editing the path data:

```bash
uv run --with fonttools python tools/build-lockup.py
```

That script holds the font, the size, the tracking, and the alignment. The word is
lower case throughout, so the eye centres it on the x-height band rather than the em box
- the script takes the x-height from the font and aligns the mark to it, because a
hand-tuned offset drifts the moment either half changes.

The typeface is **JetBrains Mono**, under the SIL Open Font License, which permits
outlining glyphs into a derivative work. It is vendored under `tools/fonts` with its
licence, so the command above works on any machine and always draws the same letters; the
script checks the font's digest first and refuses rather than emitting a wordmark in the
wrong typeface. The monospace faces macOS ships - Menlo, SF Mono, Courier - are licensed
by Apple and grant no such permission, which is a problem that only surfaces once a
project is public.

Palette: slate `#232B33`, brass `#A97C2A` (`#C79338` on dark), paper `#EEF1F3`, dark
ground `#14181B`, inverse ink `#C9D3DB`. Steel and brass are the materials of the tool.
