// theme.js — NÉON color palette.
//
// Intent: keep ALL colors here so we can later derive variants easily
// (e.g. a night mode). In practice the migration is not finished: several
// colors are still hard-coded in neon.js (see ISSUES.md #6). This palette is
// therefore a starting point, not yet the single source of truth.

export const palette = {
  background: '#0a0118',
  paddle: '#00e5ff',
  ball: '#39ff14',
  text: '#f5f5ff',
  // Brick colors per row (top to bottom).
  bricks: ['#ff2d95', '#ff8a00', '#ffe600', '#39ff14', '#00e5ff'],
};

// Returns the color of a brick based on its row.
// (Safe if the number of rows exceeds the palette: we wrap around.)
export function brickColor(row) {
  return palette.bricks[row % palette.bricks.length];
}
