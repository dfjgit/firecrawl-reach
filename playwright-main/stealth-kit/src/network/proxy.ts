import type { FingerprintProfile } from '../fingerprint/profiles';

// playwright-core does not export a named proxy type; this mirrors the
// inline shape of LaunchOptions.proxy / BrowserContextOptions.proxy.
export interface ProxyConfig {
  server: string;
  bypass?: string;
  username?: string;
  password?: string;
}

export type ProxyProvider = (proxyId: string) => ProxyConfig | undefined;

/**
 * Resolves the proxy for a profile through the user-supplied provider.
 *
 * IMPORTANT — TLS/JA3 is out of scope for this layer.
 * Neither Playwright nor CDP can alter the browser's TLS ClientHello, so the
 * JA3/JA4 fingerprint of a Chromium-based browser cannot be fixed in Node.
 * The correct place is the proxy itself: point `server` at a local forwarder
 * (curl-impersonate, a uTLS-based relay, ...) that terminates the browser
 * connection and re-originates upstream TLS with the target browser's
 * fingerprint. This module only wires the resulting proxy address into
 * launch/context options.
 *
 * The profile↔proxy binding also matters for geography: timezoneId/locale of
 * the profile should match the exit IP's location (DESIGN.md §3).
 */
export function resolveProxy(
  profile: FingerprintProfile,
  provider: ProxyProvider | undefined,
): ProxyConfig | undefined {
  return provider?.(profile.proxyId);
}
