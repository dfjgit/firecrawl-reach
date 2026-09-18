"""
navigator.* scalar surfaces: language(s), platform, hardwareConcurrency,
deviceMemory, userAgentData.

`new_context(locale=..., user_agent=...)` already fixes the HTTP-layer
values, but several JS-visible properties (platform, userAgentData, hardware
hints) are NOT covered by context options, so they are patched here to
stay consistent with the profile.

JS logic extracted verbatim from
stealth-kit/src/core/init-scripts/navigator-props.ts; the profile argument
is serialized with json.dumps and spliced in place of the
`addInitScript(fn, arg)` mechanism the TS version uses.
"""

import json

from ...fingerprint.profiles import FingerprintProfile

_NAVIGATOR_PROPS_BODY = r"""
(profile => {
  const define = (prop, value) => {
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
    getHighEntropyValues: (hints) => {
      const all = {
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
      const result = {};
      for (const hint of hints) {
        if (hint in all) result[hint] = all[hint];
      }
      return Promise.resolve(result);
    },
    toJSON: () => ({ brands, mobile: meta.mobile, platform: meta.platform }),
  };
  // Present the genuine prototype when available so instanceof passes.
  const navAny = navigator;
  if (navAny.userAgentData && navAny.userAgentData.constructor) {
    try {
      Object.setPrototypeOf(uaData, navAny.userAgentData.constructor.prototype);
    } catch (e) {
      /* older engines without NavigatorUAData — plain object is fine */
    }
  }
  define('userAgentData', uaData);
})(__PROFILE__);
"""


def make_navigator_props_init_script(profile: FingerprintProfile) -> str:
    # Keys keep the TS camelCase names so the JS body stays byte-identical.
    profile_json = json.dumps(
        {
            "locale": profile.locale,
            "platform": profile.platform,
            "hardwareConcurrency": profile.hardware_concurrency,
            "deviceMemory": profile.device_memory,
            "userAgentMetadata": profile.user_agent_metadata.to_cdp_dict(),
        }
    )
    return _NAVIGATOR_PROPS_BODY.replace("__PROFILE__", profile_json)
