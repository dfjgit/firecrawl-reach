import type { BrowserContext, Page } from 'playwright-core';
import type { FingerprintProfile } from '../fingerprint/profiles';

async function applyToPage(page: Page, profile: FingerprintProfile): Promise<void> {
  // Protocol-level overrides leave no JS-visible patching traces (no
  // defineProperty footprints), so prefer them over init-script hacks for
  // anything CDP can express.
  const session = await page.context().newCDPSession(page);
  await session.send('Network.setUserAgentOverride', {
    userAgent: profile.userAgent,
    acceptLanguage: profile.locale,
    // Keeps the sec-ch-ua-* request headers consistent with the UA and with
    // the navigator.userAgentData init script.
    userAgentMetadata: profile.userAgentMetadata,
  });
  await session.send('Emulation.setTimezoneOverride', {
    timezoneId: profile.timezoneId,
  });
}

/**
 * Applies CDP-level UA/UA-CH/timezone overrides to every page in the
 * context — existing ones and any opened later.
 */
export function attachStealthCdp(context: BrowserContext, profile: FingerprintProfile): void {
  const tryApply = (page: Page) => {
    applyToPage(page, profile).catch(err => {
      // A page that crashes or closes mid-attach should not kill the context.
      console.warn(`[stealth-kit] CDP attach failed for page: ${err}`);
    });
  };
  for (const page of context.pages()) tryApply(page);
  context.on('page', tryApply);
}
