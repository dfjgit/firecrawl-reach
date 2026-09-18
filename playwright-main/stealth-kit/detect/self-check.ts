/**
 * Self-check: launches a stealthed browser, collects the fingerprint
 * surfaces this kit patches, and asserts they match the profile.
 *
 * Usage:
 *   npm run self-check
 *
 * Environment:
 *   STEALTH_CHANNEL   'chrome' (default) | 'msedge' | 'chromium'
 *                     'chromium' = bundled build via executablePath scan of
 *                     the local ms-playwright cache (no download).
 *   STEALTH_HEADED=1  run headed (default: headless)
 *
 * Exit code 0 = all checks passed, 1 = at least one failed.
 */
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { chromium } from 'playwright-core';
import type { Browser } from 'playwright-core';
import { stealth, BUILTIN_PROFILES } from '../src/index';
import type { FingerprintProfile } from '../src/index';

// The checks assert consistency with the profile, not with the host OS, so
// the Windows sample works on any host — everything JS-visible is patched.
const profile: FingerprintProfile = BUILTIN_PROFILES[0];

function findBundledChromium(): string | undefined {
  const cache = path.join(os.homedir(), 'AppData', 'Local', 'ms-playwright');
  if (!fs.existsSync(cache)) return undefined;
  // Prefer full Chromium over headless_shell; newest revision first.
  const dirs = fs
    .readdirSync(cache)
    .filter(d => /^chromium-\d+$/.test(d))
    .sort()
    .reverse();
  for (const dir of dirs) {
    for (const sub of ['chrome-win64', 'chrome-win']) {
      const exe = path.join(cache, dir, sub, 'chrome.exe');
      if (fs.existsSync(exe)) return exe;
    }
  }
  return undefined;
}

async function launchBrowser(): Promise<{ browser: Browser; description: string }> {
  const headless = process.env.STEALTH_HEADED !== '1';
  const channel = process.env.STEALTH_CHANNEL ?? 'chrome';

  if (channel === 'chromium') {
    const executablePath = findBundledChromium();
    if (!executablePath) {
      throw new Error('STEALTH_CHANNEL=chromium but no bundled Chromium found in ms-playwright cache');
    }
    return {
      browser: await stealth.launch({ channel: undefined, headless, extraOptions: { executablePath } }),
      description: `bundled chromium (${executablePath})`,
    };
  }

  try {
    return { browser: await stealth.launch({ channel, headless }), description: `channel=${channel}` };
  } catch (err) {
    if (process.env.STEALTH_CHANNEL) throw err; // explicit choice: don't silently fall back
    console.warn(`[self-check] channel=chrome unavailable (${(err as Error).message.split('\n')[0]})`);
    console.warn('[self-check] falling back to channel=msedge');
    return { browser: await stealth.launch({ channel: 'msedge', headless }), description: 'channel=msedge' };
  }
}

interface Collected {
  webdriver: unknown;
  platform: string;
  userAgent: string;
  pluginsLength: number;
  languages: string[];
  webglRenderer: string | null;
  uaDataBrands: { brand: string; version: string }[] | null;
  timezone: string;
  hardwareConcurrency: number;
  deviceMemory: number | undefined;
}

function collect(): Collected {
  let webglRenderer: string | null = null;
  try {
    const canvas = document.createElement('canvas');
    const gl = (canvas.getContext('webgl') || canvas.getContext('experimental-webgl')) as WebGLRenderingContext | null;
    if (gl) {
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      webglRenderer = ext
        ? (gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) as string)
        : (gl.getParameter(gl.RENDERER) as string);
    }
  } catch {
    webglRenderer = null;
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const nav = navigator as any;
  return {
    webdriver: navigator.webdriver,
    platform: navigator.platform,
    userAgent: navigator.userAgent,
    pluginsLength: navigator.plugins.length,
    languages: [...navigator.languages],
    webglRenderer,
    uaDataBrands: nav.userAgentData ? nav.userAgentData.brands : null,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemory: nav.deviceMemory,
  };
}

async function main(): Promise<void> {
  const { browser, description } = await launchBrowser();
  console.log(`[self-check] browser: ${description}, profile: ${profile.id}`);

  let failed = 0;
  const check = (name: string, ok: boolean, actual: unknown) => {
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : `  (got: ${JSON.stringify(actual)})`}`);
    if (!ok) failed++;
  };

  try {
    const context = await stealth.newContext(browser, { profile });
    const page = await context.newPage();
    await page.setContent('<html><body><p>stealth self-check</p></body></html>');
    // Give the async CDP attach a beat to land before reading UA surfaces.
    await page.waitForTimeout(300);

    const c: Collected = await page.evaluate(collect);

    check('navigator.webdriver === undefined', c.webdriver === undefined, c.webdriver);
    check('navigator.platform matches profile', c.platform === profile.platform, c.platform);
    check('UA has no HeadlessChrome', !c.userAgent.includes('HeadlessChrome'), c.userAgent);
    check('UA matches profile', c.userAgent === profile.userAgent, c.userAgent);
    check('navigator.plugins non-empty', c.pluginsLength > 0, c.pluginsLength);
    check(
      'navigator.languages matches locale',
      c.languages[0] === profile.locale && c.languages.includes(profile.locale.split('-')[0]),
      c.languages,
    );
    check('WebGL renderer matches profile', c.webglRenderer === profile.webglRenderer, c.webglRenderer);
    check(
      'userAgentData brands present',
      !!c.uaDataBrands?.some(b => b.brand === 'Chromium'),
      c.uaDataBrands,
    );
    check('timezone matches profile', c.timezone === profile.timezoneId, c.timezone);
    check(
      'hardwareConcurrency matches profile',
      c.hardwareConcurrency === profile.hardwareConcurrency,
      c.hardwareConcurrency,
    );
    check('deviceMemory matches profile', c.deviceMemory === profile.deviceMemory, c.deviceMemory);

    await context.close();
  } finally {
    await browser.close();
  }

  console.log(failed === 0 ? '\nALL CHECKS PASSED' : `\n${failed} CHECK(S) FAILED`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch(err => {
  console.error(`[self-check] fatal: ${err}`);
  process.exit(1);
});
