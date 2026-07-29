// bloom.js — the neon glow pass.
//
// NÉON gets its name from the diffuse halo around every lit thing on the
// field. The halo is built as a light map: every light source in the scene
// (each live brick, plus the ball) contributes to every cell of the map, and
// the map is then stretched over the whole canvas at draw time.
//
// The map is deliberately coarse. A halo is blurry by nature, so a 160x120
// map stretched to 640x480 looks the same as a full-resolution one and is
// cheaper to build.
//
// Pure logic (`createGlowMap`, `emitters`, `computeGlow`) runs anywhere;
// `drawGlow` is the only function that touches the DOM.

export const GLOW_W = 160;
export const GLOW_H = 120;

const BRICK_POWER = 70;
const BALL_POWER = 260;

// Light falls off with the square of the distance, like real light does:
// half intensity at 30 px, a tenth of it at 90 px.
const FALLOFF = 1 / 900;

// The light sources of the current frame: every live brick, plus the ball.
export function emitters(state) {
  const sources = [];
  for (const brick of state.bricks) {
    if (!brick.alive) continue;
    sources.push({
      x: brick.x + brick.w / 2,
      y: brick.y + brick.h / 2,
      power: BRICK_POWER,
    });
  }
  sources.push({ x: state.ball.x, y: state.ball.y, power: BALL_POWER });
  return sources;
}

// Allocates the RGBA light map, reused across frames.
export function createGlowMap() {
  return new Uint8ClampedArray(GLOW_W * GLOW_H * 4);
}

// Accumulates the light map for the current frame: each cell sums the
// contribution of every source. Tinted towards magenta/blue, which is the
// palette's dominant hue.
export function computeGlow(map, state, width, height) {
  const sources = emitters(state);
  const sx = width / GLOW_W;
  const sy = height / GLOW_H;

  let i = 0;
  for (let gy = 0; gy < GLOW_H; gy++) {
    const py = gy * sy;
    for (let gx = 0; gx < GLOW_W; gx++) {
      const px = gx * sx;
      let sum = 0;
      for (let s = 0; s < sources.length; s++) {
        const light = sources[s];
        const distance = Math.hypot(px - light.x, py - light.y);
        sum += light.power / (1 + distance * distance * FALLOFF);
      }
      map[i++] = sum * 0.45;
      map[i++] = sum * 0.12;
      map[i++] = sum;
      map[i++] = 255;
    }
  }
  return map;
}

// Draws the light map over the scene, additively. The offscreen canvas holds
// the coarse map; `drawImage` stretches it to the full field.
export function drawGlow(ctx, glow, state, width, height, offscreen) {
  computeGlow(glow.map, state, width, height);
  glow.image.data.set(glow.map);
  offscreen.getContext('2d').putImageData(glow.image, 0, 0);

  const previous = ctx.globalCompositeOperation;
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = 0.55;
  ctx.drawImage(offscreen, 0, 0, width, height);
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = previous;
}
