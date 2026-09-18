import type { FingerprintProfile } from '../../fingerprint/profiles';

/**
 * navigator.* scalar surfaces: language(s), platform, hardwareConcurrency,
 * deviceMemory, userAgentData.
 *
 * `newContext({ locale, userAgent })` already fixes the HTTP-layer values,
 * but several JS-visible properties (platform, userAgentData, hardware
 * hints) are NOT covered by context options, so they are patched here to
 * stay consistent with the profile.
 */
export function makeNavigatorPropsInitScript(): (profile: FingerprintProfile) => void {
  return profile => {
    const define = (prop: string, value: unknown) => {
      Object.defineProperty(Navigator.prototype, prop, {
        get: () => value,
        configurable: true,
      });
    };

    const baseLang = profile.locale.split('-')[0];
    define('language', profile.locale);
    define('languages', Object.freeze([profile.locale, baseLang]));
    define('platform', profile.platform);
    define('hardwareConcurrency', profile.hardwareConcurrency);
    define('deviceMemory', profile.deviceMemory);

    // navigator.userAgentData (NavigatorUAData). Frozen brands mirror the
    // sec-ch-ua header; getHighEntropyValues mirrors the CDP override.
    const meta = profile.userAgentMetadata;
    const brands = Object.freeze(meta.brands.map(b => Object.freeze({ ...b })));
    const fullVersionList = Object.freeze(meta.fullVersionList.map(b => Object.freeze({ ...b })));

    const uaData = {
      brands,
      mobile: meta.mobile,
      platform: meta.platform,
      getHighEntropyValues: (hints: string[]) => {
        const all: Record<string, unknown> = {
          architecture: meta.architecture,
          bitness: meta.bitness,
          brands,
          fullVersionList,
          mobile: meta.mobile,
          model: meta.model,
          platform: meta.platform,
          platformVersion: meta.platformVersion,
          uaFullVersion: meta.fullVersion,
          wow64: false,
        };
        const result: Record<string, unknown> = {};
        for (const hint of hints) {
          if (hint in all) result[hint] = all[hint];
        }
        return Promise.resolve(result);
      },
      toJSON: () => ({ brands, mobile: meta.mobile, platform: meta.platform }),
    };
    // Present the genuine prototype when available so instanceof passes.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const navAny = navigator as any;
    if (navAny.userAgentData && navAny.userAgentData.constructor) {
      try {
        Object.setPrototypeOf(uaData, navAny.userAgentData.constructor.prototype);
      } catch {
        /* older engines without NavigatorUAData — plain object is fine */
      }
    }
    define('userAgentData', uaData);
  };
}
