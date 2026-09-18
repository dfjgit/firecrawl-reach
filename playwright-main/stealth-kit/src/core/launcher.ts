import { chromium } from 'playwright-core';
import type { Browser, LaunchOptions } from 'playwright-core';
import type { ProxyConfig } from '../network/proxy';

/**
 * Default-arg switches Playwright adds that we deliberately strip.
 *
 * `ignoreDefaultArgs` is an EXACT string match against Playwright's internal
 * switch list (chromiumSwitches.ts), so every entry here must be verified
 * against the installed Playwright version on upgrade — that is why this
 * list is an explicit constant with per-entry rationale, not inline config.
 */
export const STRIPPED_DEFAULT_ARGS: readonly string[] = [
  // Real users have extensions; a browser that cannot load any stands out.
  // (Also removes the need for --disable-component-extensions-with-background-pages.)
  '--disable-extensions',
  '--disable-component-extensions-with-background-pages',
  // Disabling popup blocking changes window.open behavior in detectable ways.
  '--disable-popup-blocking',
];

/** Extra args appended on top of Playwright's defaults. */
export const EXTRA_ARGS: readonly string[] = [
  // Belt and braces: Playwright does not pass this today, but if a future
  // version (or a channel build) ever does, automation-controlled Blink
  // features stay off.
  '--disable-blink-features=AutomationControlled',
];

export interface StealthLaunchOptions {
  /**
   * Browser channel. Defaults to 'chrome' — driving the real installed
   * Chrome binary sidesteps the binary-level differences of bundled Chromium
   * (DESIGN.md §0.3). Pass 'msedge' or omit for bundled Chromium.
   */
  channel?: string;
  /** Defaults to false: headed browsers look far less automated. */
  headless?: boolean;
  args?: string[];
  ignoreDefaultArgs?: string[];
  proxy?: ProxyConfig;
  /** Escape hatch for any other playwright-core launch option. */
  extraOptions?: Omit<LaunchOptions, 'channel' | 'headless' | 'args' | 'ignoreDefaultArgs' | 'proxy'>;
}

export async function launch(options: StealthLaunchOptions = {}): Promise<Browser> {
  const {
    channel = 'chrome',
    headless = false,
    args = [],
    ignoreDefaultArgs = [...STRIPPED_DEFAULT_ARGS],
    proxy,
    extraOptions = {},
  } = options;

  return chromium.launch({
    channel,
    headless,
    proxy,
    args: [...EXTRA_ARGS, ...args],
    ignoreDefaultArgs,
    ...extraOptions,
  });
}
