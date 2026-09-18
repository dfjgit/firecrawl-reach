import type { Page } from 'playwright-core';

/**
 * Human-like scrolling: several wheel ticks with varied deltas and pauses
 * instead of one instant jump to a target offset.
 */

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
const rand = (min: number, max: number) => min + Math.random() * (max - min);

export interface HumanScrollOptions {
  /** Total vertical distance in px. Default: one viewport-ish, downward. */
  distance?: number;
  direction?: 'down' | 'up';
  /** Split the distance into at most this many ticks. */
  maxTicks?: number;
}

export async function humanScroll(page: Page, options: HumanScrollOptions = {}): Promise<void> {
  const sign = options.direction === 'up' ? -1 : 1;
  let remaining = Math.abs(options.distance ?? rand(400, 900));
  const maxTicks = options.maxTicks ?? 8;
  let ticks = 0;

  while (remaining > 0 && ticks < maxTicks) {
    // Early ticks are larger; the last ones fine-tune, like a real reader.
    const delta = ticks === 0 ? rand(0.4, 0.6) * remaining : rand(0.15, 0.45) * remaining;
    await page.mouse.wheel(0, sign * Math.max(40, delta));
    remaining -= delta;
    ticks++;
    await sleep(rand(120, 450));
  }
}
