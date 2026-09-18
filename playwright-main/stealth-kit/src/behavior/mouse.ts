import type { Page } from 'playwright-core';

/**
 * Human-like mouse movement: cubic Bézier path + sub-pixel jitter.
 *
 * page.mouse.click teleports the cursor — a synthetic event pattern trivial
 * to spot in event-stream analysis. Sensitive interactions should go through
 * humanClick/hover instead.
 */

// Last known cursor position per page; Playwright doesn't expose it.
const lastPosition = new WeakMap<Page, { x: number; y: number }>();

const rand = (min: number, max: number) => min + Math.random() * (max - min);
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

function bezierPoint(
  t: number,
  p0: { x: number; y: number },
  p1: { x: number; y: number },
  p2: { x: number; y: number },
  p3: { x: number; y: number },
): { x: number; y: number } {
  const u = 1 - t;
  return {
    x: u ** 3 * p0.x + 3 * u ** 2 * t * p1.x + 3 * u * t ** 2 * p2.x + t ** 3 * p3.x,
    y: u ** 3 * p0.y + 3 * u ** 2 * t * p1.y + 3 * u * t ** 2 * p2.y + t ** 3 * p3.y,
  };
}

/** Moves the cursor along a curved path to (x, y). */
export async function moveMouse(
  page: Page,
  x: number,
  y: number,
  options: { steps?: number } = {},
): Promise<void> {
  const from = lastPosition.get(page) ?? { x: rand(0, 400), y: rand(0, 300) };
  const distance = Math.hypot(x - from.x, y - from.y);
  const steps = options.steps ?? Math.max(10, Math.min(60, Math.round(distance / 15)));

  // Control points scattered around the straight line give a natural arc.
  const spread = distance / 4;
  const c1 = {
    x: from.x + (x - from.x) * rand(0.2, 0.4) + rand(-spread, spread),
    y: from.y + (y - from.y) * rand(0.2, 0.4) + rand(-spread, spread),
  };
  const c2 = {
    x: from.x + (x - from.x) * rand(0.6, 0.8) + rand(-spread, spread),
    y: from.y + (y - from.y) * rand(0.6, 0.8) + rand(-spread, spread),
  };

  for (let i = 1; i <= steps; i++) {
    // Ease-in-out: humans accelerate then decelerate into the target.
    const t = i / steps;
    const eased = t * t * (3 - 2 * t);
    const p = bezierPoint(eased, from, c1, c2, { x, y });
    await page.mouse.move(p.x + rand(-0.8, 0.8), p.y + rand(-0.8, 0.8));
    await sleep(rand(2, 12));
  }
  lastPosition.set(page, { x, y });
}

/**
 * Hovers to a human-plausible point inside the element, then clicks with
 * realistic press timing.
 */
export async function humanClick(
  page: Page,
  selector: string,
  options: { button?: 'left' | 'right' | 'middle'; timeout?: number } = {},
): Promise<void> {
  const locator = page.locator(selector).first();
  await locator.waitFor({ state: 'visible', timeout: options.timeout });
  const box = await locator.boundingBox();
  if (!box) throw new Error(`humanClick: no bounding box for "${selector}"`);

  // Aim near the center with a bell-ish offset — never the exact geometric
  // center every time (a classic bot tell).
  const target = {
    x: box.x + box.width * (0.5 + rand(-0.2, 0.2)),
    y: box.y + box.height * (0.5 + rand(-0.2, 0.2)),
  };
  await moveMouse(page, target.x, target.y);
  await sleep(rand(80, 250)); // dwell on the hover before committing
  await page.mouse.down({ button: options.button ?? 'left' });
  await sleep(rand(45, 120)); // human press duration
  await page.mouse.up({ button: options.button ?? 'left' });
}
