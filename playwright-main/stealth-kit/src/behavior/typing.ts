import type { Page } from 'playwright-core';

/**
 * Human-like typing: log-normal inter-key delays + occasional typos with
 * backspace correction.
 *
 * Uniform delays (page.type's fixed `delay` option) produce a metronome
 * pattern; real keystroke intervals are heavy-tailed, hence log-normal.
 */

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
const rand = (min: number, max: number) => min + Math.random() * (max - min);

/** Log-normal sample; mu/sigma tuned for ~60-220ms typical gaps. */
function keyDelay(): number {
  const mu = 4.9;
  const sigma = 0.45;
  const z = Math.sqrt(-2 * Math.log(Math.random())) * Math.cos(2 * Math.PI * Math.random());
  return Math.min(600, Math.exp(mu + sigma * z));
}

// Neighbor keys on a QWERTY layout, for plausible typos.
const NEIGHBORS: Record<string, string[]> = Object.fromEntries(
  Object.entries({
    a: 'sqwz', b: 'vghn', c: 'xdfv', d: 'serfcx', e: 'wrsd', f: 'drtgvc', g: 'ftyhbv',
    h: 'gyujnb', i: 'ujko', j: 'huikmn', k: 'jiolm', l: 'kop', m: 'njk', n: 'bhjm',
    o: 'iklp', p: 'ol', q: 'wa', r: 'edft', s: 'awedxz', t: 'rfgy', u: 'yhji',
    v: 'cfgb', w: 'qase', x: 'zsdc', y: 'tghu', z: 'asx',
  }).map(([k, v]) => [k, v.split('')]),
);

export interface HumanTypeOptions {
  /** Probability of a typo+backspace per character. Default 0.02. */
  typoRate?: number;
  /** Focus the element before typing. Default true. */
  clickFirst?: boolean;
}

export async function humanType(
  page: Page,
  selector: string,
  text: string,
  options: HumanTypeOptions = {},
): Promise<void> {
  const typoRate = options.typoRate ?? 0.02;
  if (options.clickFirst ?? true) {
    await page.locator(selector).first().click();
    await sleep(rand(150, 400));
  }

  for (const ch of text) {
    if (Math.random() < typoRate && NEIGHBORS[ch.toLowerCase()]) {
      const neighbors = NEIGHBORS[ch.toLowerCase()];
      const wrong = neighbors[Math.floor(Math.random() * neighbors.length)];
      await page.keyboard.type(wrong);
      await sleep(keyDelay() * 1.5); // noticing the mistake takes a moment
      await page.keyboard.press('Backspace');
      await sleep(keyDelay());
    }
    await page.keyboard.type(ch);
    await sleep(keyDelay());
  }
}
