// neon.js — NÉON, a minimalist brick breaker (zero dependencies).
//
// This file INTENTIONALLY mixes game logic and rendering in a few places
// (see ISSUES.md #2): brick collision detection currently lives inside the
// draw loop `frame()`. This is deliberate debt, ripe for a refactor.
//
// The module runs nothing on import: only `boot()` touches the DOM. Pure
// logic can therefore be imported from the tests (`node --test`) without a
// browser.

import { palette, brickColor } from './theme.js';
import { createGlowMap, drawGlow, GLOW_W, GLOW_H } from './bloom.js';

// --- Field constants -------------------------------------------------------

export const WIDTH = 640;
export const HEIGHT = 480;

export const PADDLE_W = 96;
export const PADDLE_H = 12;
export const PADDLE_Y = HEIGHT - 32;

export const BALL_R = 7;

export const BRICK_ROWS = 5;
export const BRICK_COLS = 8;
export const BRICK_W = 72;
export const BRICK_H = 20;
export const BRICK_GAP = 8;
export const BRICK_TOP = 48;

const STORAGE_KEY = 'neon.best';

// --- State construction ----------------------------------------------------

// Builds the brick grid for a given level.
// The higher the level, the more points bricks are worth.
export function makeBricks(level = 1) {
  const bricks = [];
  const offsetX = (WIDTH - (BRICK_COLS * (BRICK_W + BRICK_GAP) - BRICK_GAP)) / 2;
  for (let row = 0; row < BRICK_ROWS; row++) {
    for (let col = 0; col < BRICK_COLS; col++) {
      bricks.push({
        x: offsetX + col * (BRICK_W + BRICK_GAP),
        y: BRICK_TOP + row * (BRICK_H + BRICK_GAP),
        w: BRICK_W,
        h: BRICK_H,
        row,
        alive: true,
        points: (BRICK_ROWS - row) * 10 * level,
      });
    }
  }
  return bricks;
}

// Ball speed as a function of the level (ramps up with each level).
export function ballSpeed(level = 1) {
  return 260 + (level - 1) * 90;
}

// Creates a fresh game state.
export function createState(level = 1) {
  const speed = ballSpeed(level);
  return {
    level,
    lives: 3,
    score: 0,
    combo: 0,
    best: 0,
    running: false,
    paddle: { x: (WIDTH - PADDLE_W) / 2, y: PADDLE_Y, w: PADDLE_W, h: PADDLE_H },
    ball: { x: WIDTH / 2, y: PADDLE_Y - BALL_R - 2, r: BALL_R, vx: speed * 0.5, vy: -speed },
    bricks: makeBricks(level),
  };
}

// --- Collision (tested primitive) ------------------------------------------

// True if the ball (circle approximated by its box) overlaps the brick.
export function collides(ball, brick) {
  return (
    ball.x + ball.r > brick.x &&
    ball.x - ball.r < brick.x + brick.w &&
    ball.y + ball.r > brick.y &&
    ball.y - ball.r < brick.y + brick.h
  );
}

// Resolves collisions between the ball and all active bricks.
// Handles scoring, brick destruction, and physical bouncing.
export function resolveBrickCollisions(state) {
  const { ball, bricks } = state;
  for (const brick of bricks) {
    if (!brick.alive) continue;

    if (collides(ball, brick)) {
      brick.alive = false;
      state.combo += 1;
      state.score += scoreForBrick(brick, state.combo);

      const left = Math.max(ball.x - ball.r, brick.x);
      const right = Math.min(ball.x + ball.r, brick.x + brick.w);
      const top = Math.max(ball.y - ball.r, brick.y);
      const bottom = Math.min(ball.y + ball.r, brick.y + brick.h);

      if (right - left < bottom - top) {
        if (ball.vx > 0) {
          ball.vx = -Math.abs(ball.vx);
          ball.x = brick.x - ball.r;
        } else {
          ball.vx = Math.abs(ball.vx);
          ball.x = brick.x + brick.w + ball.r;
        }
      } else {
        if (ball.vy > 0) {
          ball.vy = -Math.abs(ball.vy);
          ball.y = brick.y - ball.r;
        } else {
          ball.vy = Math.abs(ball.vy);
          ball.y = brick.y + brick.h + ball.r;
        }
      }
    }
  }
}

// --- Score & combo (NOT covered by tests — debt #5) ------------------------

// Combo multiplier. UNBOUNDED today: a long combo makes the score blow up
// uncontrollably (see #5, expected improvement = clamp it).
export function comboMultiplier(combo) {
  return 1 + combo * 0.5;
}

// Points earned for breaking a brick, combo included.
export function scoreForBrick(brick, combo) {
  return Math.round(brick.points * comboMultiplier(combo));
}

// --- Best score persistence ------------------------------------------------

// Reads the best score from a localStorage-compatible storage.
// Robust to corrupted or missing storage: returns 0 instead of throwing.
export function loadBest(storage) {
  if (!storage) return 0;
  try {
    const raw = storage.getItem(STORAGE_KEY);
    const value = Number.parseInt(raw, 10);
    return Number.isFinite(value) && value > 0 ? value : 0;
  } catch {
    return 0;
  }
}

// Writes the best score if the current score beats it. Returns the updated best.
export function saveBest(storage, score) {
  const best = loadBest(storage);
  if (score > best && storage) {
    try {
      storage.setItem(STORAGE_KEY, String(score));
    } catch {
      // storage unavailable (private mode, quota): silently ignore
    }
    return score;
  }
  return best;
}

// --- Physics (movement + wall/paddle bounces) ------------------------------

// Advances the ball by a time step `dt` (in seconds) and handles bounces off
// the walls and the paddle. Does NOT handle bricks: that responsibility still
// lives in the render loop `frame()` (debt #2).
// Returns `true` if the ball is lost (exits through the bottom).
export function step(state, dt) {
  const { ball, paddle } = state;

  ball.x += ball.vx * dt;
  ball.y += ball.vy * dt;

  // Side walls
  if (ball.x - ball.r < 0) {
    ball.x = ball.r;
    ball.vx = Math.abs(ball.vx);
  } else if (ball.x + ball.r > WIDTH) {
    ball.x = WIDTH - ball.r;
    ball.vx = -Math.abs(ball.vx);
  }

  // Ceiling
  if (ball.y - ball.r < 0) {
    ball.y = ball.r;
    ball.vy = Math.abs(ball.vy);
  }

  // Paddle
  if (
    ball.vy > 0 &&
    ball.y + ball.r >= paddle.y &&
    ball.y + ball.r <= paddle.y + paddle.h &&
    ball.x >= paddle.x &&
    ball.x <= paddle.x + paddle.w
  ) {
    ball.y = paddle.y - ball.r;
    ball.vy = -Math.abs(ball.vy);
    // The rebound angle depends on where the paddle was hit.
    const hit = (ball.x - (paddle.x + paddle.w / 2)) / (paddle.w / 2);
    ball.vx = ballSpeed(state.level) * 0.6 * hit;
    state.combo = 0; // touching the paddle resets the combo
  }

  // Bricks
  resolveBrickCollisions(state);

  // Exit through the bottom: ball lost
  if (ball.y - ball.r > HEIGHT) {
    return true;
  }
  return false;
}

// --- Rendering (DOM): brick collision logic STILL mixed in here ------------

// Main render loop. For each live brick we test collision AND draw in the same
// pass: this is where logic and rendering are entangled.
function frame(ctx, state) {
  // Background
  ctx.fillStyle = palette.background;
  ctx.fillRect(0, 0, WIDTH, HEIGHT);

  // Bricks
  for (const brick of state.bricks) {
    if (!brick.alive) continue;

    ctx.fillStyle = brickColor(brick.row);
    ctx.fillRect(brick.x, brick.y, brick.w, brick.h);
  }

  // Paddle — color hard-coded instead of using the palette (inconsistency #6).
  ctx.fillStyle = '#00e5ff';
  ctx.fillRect(state.paddle.x, state.paddle.y, state.paddle.w, state.paddle.h);

  // Ball — also hard-coded (#6).
  ctx.fillStyle = '#39ff14';
  ctx.beginPath();
  ctx.arc(state.ball.x, state.ball.y, state.ball.r, 0, Math.PI * 2);
  ctx.fill();

  // HUD
  ctx.fillStyle = palette.text;
  ctx.font = '16px monospace';
  ctx.fillText(`Score ${state.score}`, 12, 24);
  ctx.fillText(`Best ${state.best}`, WIDTH - 110, 24);
  ctx.fillText(`Lives ${state.lives}`, WIDTH / 2 - 30, 24);
}

// --- Boot (browser entry point) --------------------------------------------

// Starts the game: wires up the canvas, the mouse and the animation loop.
// The only function that depends on the DOM; never called on import.
export function boot(doc = document) {
  const canvas = doc.getElementById('game');
  const ctx = canvas.getContext('2d');
  canvas.width = WIDTH;
  canvas.height = HEIGHT;

  const state = createState(1);
  state.best = loadBest(doc.defaultView?.localStorage ?? globalThis.localStorage);
  state.running = true;

  // Glow pass: a coarse offscreen light map, stretched over the field.
  const offscreen = doc.createElement('canvas');
  offscreen.width = GLOW_W;
  offscreen.height = GLOW_H;
  const glow = {
    map: createGlowMap(),
    image: offscreen.getContext('2d').createImageData(GLOW_W, GLOW_H),
  };

  // Move the paddle with the mouse.
  canvas.addEventListener('mousemove', (e) => {
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left - state.paddle.w / 2;
    state.paddle.x = Math.max(0, Math.min(WIDTH - state.paddle.w, x));
  });

  let last = performance.now();
  function loop(now) {
    const dt = Math.min((now - last) / 1000, 0.05);
    last = now;

    if (state.running) {
      const lost = step(state, dt);
      if (lost) {
        state.lives -= 1;
        state.combo = 0;
        if (state.lives <= 0) {
          state.best = saveBest(globalThis.localStorage, state.score);
          Object.assign(state, createState(state.level));
          state.best = loadBest(globalThis.localStorage);
        } else {
          // relaunch the ball
          state.ball = createState(state.level).ball;
        }
      }

      // Level cleared: all bricks broken
      if (state.bricks.every((b) => !b.alive)) {
        state.level += 1;
        state.bricks = makeBricks(state.level);
        state.ball = createState(state.level).ball;
      }
    }

    frame(ctx, state);
    drawGlow(ctx, glow, state, WIDTH, HEIGHT, offscreen);
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);

  return state;
}
