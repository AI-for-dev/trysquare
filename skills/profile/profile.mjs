#!/usr/bin/env node
// profile.mjs — where does NÉON's per-frame JavaScript time actually go?
//
// Answers that with numbers, and nothing else. It does not say what to fix:
// reading a profile and deciding what to do about it is the caller's job.
//
//   node profile.mjs                 profile at the shipped brick count
//   node profile.mjs --hz 120        use a 120 Hz frame budget (8.33 ms)
//   node profile.mjs --scale         also sweep the brick count, and report
//                                    where each part would start to matter
//   node profile.mjs --reps 15       more repetitions, tighter median
//
// Measuring a microbenchmark correctly needs four things, and skipping any of
// them produces a number that looks fine and is wrong:
//
//   1. WARMUP     — the first calls run interpreted; the optimising compiler
//                   only kicks in after the function has been seen enough.
//   2. A SINK     — the result of every call is accumulated into a variable
//                   that is read at the end, so the optimiser cannot delete
//                   work whose result nobody uses.
//   3. REPETITIONS — one sample measures the machine's mood, not the code.
//   4. A MEDIAN   — the mean follows the outliers, the median does not.
//
// The single place to adapt for another repository is `postes()`, at the
// bottom of this file.

import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// ─── Options ────────────────────────────────────────────────────────────────

const argv = process.argv.slice(2);
const flag = (name) => argv.includes(name);
const value = (name, fallback) => {
  const i = argv.indexOf(name);
  return i >= 0 && argv[i + 1] ? Number(argv[i + 1]) : fallback;
};

const HZ = value('--hz', 60);
const BUDGET_MS = 1000 / HZ;
const REPS = value('--reps', 7);
const SCALE = flag('--scale');

// The repository root: two levels up from .pi/skills/profile/, or $NEON.
const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = process.env.NEON
  ? resolve(process.env.NEON)
  : resolve(HERE, '..', '..', '..');

// ─── Measurement ────────────────────────────────────────────────────────────

const median = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

// Runs `fn` and returns the per-call duration in milliseconds: min, median and
// max over `REPS` samples. `fn` must return a number, which is accumulated so
// the optimiser cannot elide the call.
function mesurer(fn, iterations) {
  const warmup = Math.min(iterations, 2000);
  let sink = 0;

  for (let i = 0; i < warmup; i++) sink += fn(i);

  const samples = [];
  for (let r = 0; r < REPS; r++) {
    const t0 = performance.now();
    for (let i = 0; i < iterations; i++) sink += fn(i);
    samples.push((performance.now() - t0) / iterations);
  }

  // Reading the sink is what makes it a sink. The branch never fires.
  if (!Number.isFinite(sink)) throw new Error('mesure invalide');

  return { min: Math.min(...samples), med: median(samples), max: Math.max(...samples) };
}

// A cheap part needs many iterations to be measurable, an expensive one needs
// few. Aiming for ~50 ms per sample keeps both honest without being slow.
function iterationsPour(fn) {
  const t0 = performance.now();
  let sink = 0;
  let n = 0;
  while (performance.now() - t0 < 5 && n < 100_000) {
    sink += fn(n);
    n++;
  }
  if (!Number.isFinite(sink)) throw new Error('sondage invalide');
  const parAppel = (performance.now() - t0) / Math.max(n, 1);
  return Math.max(5, Math.min(50_000, Math.round(50 / Math.max(parAppel, 1e-6))));
}

// ─── Rendering ──────────────────────────────────────────────────────────────

const ms = (v) => (v >= 0.01 ? v.toFixed(4) : v.toExponential(2));
const pct = (v) => (v >= 0.01 ? v.toFixed(2) : v.toExponential(2));

function ligne(cells, widths) {
  return '| ' + cells.map((c, i) => String(c).padEnd(widths[i])).join(' | ') + ' |';
}

function table(entetes, lignes) {
  const widths = entetes.map((h, i) =>
    Math.max(h.length, ...lignes.map((l) => String(l[i]).length)));
  return [
    ligne(entetes, widths),
    '| ' + widths.map((w) => '-'.repeat(w)).join(' | ') + ' |',
    ...lignes.map((l) => ligne(l, widths)),
  ].join('\n');
}

// ─── Main ───────────────────────────────────────────────────────────────────

const { postes, echelle } = await definitions();

const mesures = [];
for (const poste of await postes()) {
  const it = iterationsPour(poste.run);
  mesures.push({ nom: poste.nom, ...mesurer(poste.run, it), iterations: it });
}

const totalJS = mesures.reduce((s, m) => s + m.med, 0);

console.log(`# Per-frame profile — ${REPO.split('/').pop()}`);
console.log('');
console.log(`Frame budget: **${BUDGET_MS.toFixed(2)} ms** (${HZ} Hz). `
  + `${REPS} samples per part, median reported. Node ${process.version}.`);
console.log('');
console.log(table(
  ['part', 'median (ms)', 'min', 'max', '% of frame budget', '% of per-frame JS'],
  mesures
    .slice()
    .sort((a, b) => b.med - a.med)
    .map((m) => [
      m.nom,
      ms(m.med),
      ms(m.min),
      ms(m.max),
      pct((m.med / BUDGET_MS) * 100) + ' %',
      pct((m.med / totalJS) * 100) + ' %',
    ])));
console.log('');
console.log(`Total per-frame JavaScript: **${ms(totalJS)} ms**, `
  + `${pct((totalJS / BUDGET_MS) * 100)} % of the budget.`);

if (SCALE) {
  const points = await echelle();
  console.log('');
  console.log('## Scaling with the brick count');
  console.log('');

  // Raw measurements are kept as numbers: formatting them and reading the
  // strings back is how a rounded 0.00 turns into a division by zero.
  const brut = new Map();
  for (const { n, parts } of points) {
    for (const p of parts) {
      const r = mesurer(p.run, iterationsPour(p.run));
      if (!brut.has(p.nom)) brut.set(p.nom, []);
      brut.get(p.nom).push({ n, med: r.med });
    }
  }

  console.log(table(
    ['part', 'bricks', 'median (ms)', '% of budget', 'ns per brick'],
    [...brut].flatMap(([nom, pts]) => pts.map(({ n, med }) => [
      nom, n, ms(med), pct((med / BUDGET_MS) * 100) + ' %', ((med * 1e6) / n).toFixed(2),
    ]))));

  console.log('');
  console.log('Extrapolated from the two widest measured points, assuming the '
    + 'cost keeps growing at the same rate. A part whose cost does not follow '
    + 'the brick count is reported as such rather than extrapolated.');
  console.log('');
  console.log(table(
    ['part', 'growth', 'bricks for 1 % of the budget', 'bricks to fill the budget'],
    [...brut].map(([nom, pts]) => {
      const bas = pts[0];
      const haut = pts[pts.length - 1];
      const facteurN = haut.n / bas.n;
      const facteurT = haut.med / bas.med;

      // Below a tenth of the growth in n, the part is not driven by the brick
      // count: `bricks.every(...)` returns on the first live brick, for
      // instance, so its cost is the same at 40 and at 40 000.
      if (facteurT < facteurN / 10) {
        return [nom, 'flat', 'does not follow the brick count', '—'];
      }
      const ns = (haut.med * 1e6) / haut.n;
      return [
        nom,
        `x${facteurT.toFixed(1)} for x${facteurN} bricks`,
        Math.round((0.01 * BUDGET_MS * 1e6) / ns).toLocaleString('en-US'),
        Math.round((BUDGET_MS * 1e6) / ns).toLocaleString('en-US'),
      ];
    })));
}

// ─── The parts being measured ───────────────────────────────────────────────
// THE ONLY PLACE TO ADAPT FOR ANOTHER REPOSITORY.
//
// One entry per piece of work the frame does. `run` must return a number, and
// must do the same amount of work on every call: a part that consumes its
// input (destroying bricks, for instance) measures the tail of its own work
// rather than a frame.

async function definitions() {
  const neon = await import(`file://${REPO}/game/neon.js`);
  const bloom = await import(`file://${REPO}/game/bloom.js`).catch(() => null);

  const grille = (n) => {
    const bricks = [];
    const cols = Math.max(1, Math.round(Math.sqrt(n * 1.8)));
    for (let i = 0; i < n; i++) {
      bricks.push({
        x: (i % cols) * (neon.BRICK_W + neon.BRICK_GAP),
        y: neon.BRICK_TOP + Math.floor(i / cols) * (neon.BRICK_H + neon.BRICK_GAP),
        w: neon.BRICK_W, h: neon.BRICK_H, row: i % 5, alive: true, points: 10,
      });
    }
    return bricks;
  };

  const partsPour = (bricks) => {
    const state = { ...neon.createState(1), bricks };
    const ball = state.ball;
    const parts = [
      {
        nom: 'brick collision scan',
        run: (i) => {
          // The ball is moved so the scan is not measured on one fixed spot.
          ball.x = (i * 37) % neon.WIDTH;
          ball.y = (i * 17) % neon.HEIGHT;
          let hits = 0;
          for (const brick of bricks) {
            if (!brick.alive) continue;
            if (neon.collides(ball, brick)) hits++;
          }
          return hits;
        },
      },
      {
        nom: 'level-cleared check',
        run: () => (bricks.every((b) => !b.alive) ? 1 : 0),
      },
    ];
    if (bloom) {
      const map = bloom.createGlowMap();
      parts.push({
        nom: 'glow light map',
        run: () => {
          bloom.computeGlow(map, state, neon.WIDTH, neon.HEIGHT);
          return map[0];
        },
      });
      parts.push({
        nom: 'glow emitter list',
        run: () => bloom.emitters(state).length,
      });
    }
    return parts;
  };

  return {
    postes: async () => {
      const state = neon.createState(1);
      const parts = partsPour(state.bricks);
      // Physics is measured on its own state, because `step` mutates the ball.
      const propre = neon.createState(1);
      parts.push({
        nom: 'physics step',
        run: () => {
          neon.step(propre, 1 / 60);
          return propre.ball.x;
        },
      });
      return parts;
    },
    // The sweep. `maxN` caps a part whose cost grows too fast to be swept far:
    // the glow is O(cells x emitters), so 40 000 bricks would take minutes.
    echelle: async () => {
      const maxN = { 'glow light map': 4000 };
      return [40, 400, 4000, 40000].map((n) => ({
        n,
        parts: partsPour(grille(n)).filter(
          (p) => p.nom !== 'glow emitter list' && n <= (maxN[p.nom] ?? Infinity)),
      }));
    },
  };
}
