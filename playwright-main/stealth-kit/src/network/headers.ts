import type { BrowserContext } from 'playwright-core';
import type { FingerprintProfile } from '../fingerprint/profiles';

function secChUa(profile: FingerprintProfile): string {
  return profile.userAgentMetadata.brands.map(b => `"${b.brand}";v="${b.version}"`).join(', ');
}

/**
 * Normalizes request headers to the profile via route interception.
 *
 * Known limits (DESIGN.md §2.4):
 *  - Header *order* on the wire cannot be controlled through route.continue;
 *    Chromium serializes headers itself. Targets that fingerprint header
 *    order or HTTP/2 frames need the proxy layer (see network/proxy.ts).
 *  - Some forbidden headers are passed through untouched by Chromium.
 */
export function setupHeaderNormalization(
  context: BrowserContext,
  profile: FingerprintProfile,
): void {
  const baseLang = profile.locale.split('-')[0];
  const acceptLanguage = `${profile.locale},${baseLang};q=0.9`;

  void context.route('**/*', route => {
    const headers = {
      ...route.request().headers(),
      'sec-ch-ua': secChUa(profile),
      'sec-ch-ua-platform': `"${profile.userAgentMetadata.platform}"`,
      'sec-ch-ua-mobile': profile.userAgentMetadata.mobile ? '?1' : '?0',
      'accept-language': acceptLanguage,
    };
    void route.continue({ headers });
  });
}
