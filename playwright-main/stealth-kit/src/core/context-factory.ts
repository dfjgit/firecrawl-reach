import type { Browser, BrowserContext, BrowserContextOptions } from 'playwright-core';
import type { FingerprintProfile } from '../fingerprint/profiles';
import { validateProfile } from '../fingerprint/validator';
import { attachStealthCdp } from './cdp';
import { setupHeaderNormalization } from '../network/headers';
import type { ProxyProvider } from '../network/proxy';
import { makeWebdriverInitScript } from './init-scripts/webdriver';
import { makePluginsInitScript } from './init-scripts/plugins';
import { makePermissionsInitScript } from './init-scripts/permissions';
import { makeWebglInitScript } from './init-scripts/webgl';
import { makeCanvasInitScript } from './init-scripts/canvas';
import { makeNavigatorPropsInitScript } from './init-scripts/navigator-props';

export interface StealthContextOptions {
  profile: FingerprintProfile;
  /**
   * Resolves profile.proxyId to a Playwright proxy config. When omitted (or
   * when it returns undefined) the context goes direct.
   */
  proxyProvider?: ProxyProvider;
  /** Merged over the profile-derived context options; wins on conflict. */
  overrides?: Partial<BrowserContextOptions>;
}

/**
 * Creates a browser context whose every fingerprint surface is driven by one
 * validated profile:
 *   validate → context options → init scripts → CDP overrides → header routing.
 */
export async function newContext(
  browser: Browser,
  options: StealthContextOptions,
): Promise<BrowserContext> {
  const { profile, proxyProvider, overrides = {} } = options;

  // Fail fast on a self-contradicting identity before touching the browser.
  validateProfile(profile);

  const contextOptions: BrowserContextOptions = {
    userAgent: profile.userAgent,
    viewport: profile.viewport,
    screen: profile.screen ?? profile.viewport,
    deviceScaleFactor: profile.deviceScaleFactor,
    locale: profile.locale,
    timezoneId: profile.timezoneId,
    proxy: proxyProvider?.(profile.proxyId),
    ...overrides,
  };

  const context = await browser.newContext(contextOptions);

  // addInitScript runs in the main world via CDP
  // Page.addScriptToEvaluateOnNewDocument — no DOM/script-tag traces.
  await context.addInitScript(makeWebdriverInitScript());
  await context.addInitScript(makePluginsInitScript());
  await context.addInitScript(makePermissionsInitScript());
  await context.addInitScript(makeWebglInitScript(), profile);
  await context.addInitScript(makeCanvasInitScript(), profile);
  await context.addInitScript(makeNavigatorPropsInitScript(), profile);

  attachStealthCdp(context, profile);
  setupHeaderNormalization(context, profile);

  return context;
}
