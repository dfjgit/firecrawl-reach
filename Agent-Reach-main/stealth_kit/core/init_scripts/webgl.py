"""
WebGL UNMASKED_VENDOR_WEBGL / UNMASKED_RENDERER_WEBGL

The raw GPU string leaks the real hardware (or "SwiftShader"/"Google
SwiftShader" under headless, an instant giveaway). We intercept
getParameter for the two unmasked constants on both WebGL1 and WebGL2 and
answer with the profile's string — which must match the OS claimed by the
UA (ANGLE/D3D on Windows, Metal/Apple GPU on macOS).

JS logic extracted verbatim from stealth-kit/src/core/init-scripts/webgl.ts;
the profile argument is serialized with json.dumps and spliced in place of
the `addInitScript(fn, arg)` mechanism the TS version uses.
"""

import json

from ...fingerprint.profiles import FingerprintProfile

_WEBGL_BODY = r"""
(profile => {
  // WEBGL_debug_renderer_info constants. Detectors fetch the extension and
  // then call getParameter(ext.UNMASKED_*), which lands here.
  const UNMASKED_VENDOR = 0x9245;
  const UNMASKED_RENDERER = 0x9246;

  const patch = (proto) => {
    if (!proto) return;
    const anyProto = proto;
    const original = anyProto.getParameter;
    if (typeof original !== 'function') return;
    const patched = function (pname) {
      if (pname === UNMASKED_VENDOR) return profile.webglVendor;
      if (pname === UNMASKED_RENDERER) return profile.webglRenderer;
      return original.call(this, pname);
    };
    Object.defineProperty(patched, 'toString', {
      value: () => 'function getParameter() { [native code] }',
      configurable: true,
    });
    anyProto.getParameter = patched;
  };

  if (typeof WebGLRenderingContext !== 'undefined') patch(WebGLRenderingContext.prototype);
  if (typeof WebGL2RenderingContext !== 'undefined') patch(WebGL2RenderingContext.prototype);
})(__PROFILE__);
"""


def make_webgl_init_script(profile: FingerprintProfile) -> str:
    # Keys keep the TS camelCase names so the JS body stays byte-identical.
    profile_json = json.dumps(
        {
            "webglVendor": profile.webgl_vendor,
            "webglRenderer": profile.webgl_renderer,
        }
    )
    return _WEBGL_BODY.replace("__PROFILE__", profile_json)
