import type { FingerprintProfile } from '../../fingerprint/profiles';

/**
 * WebGL UNMASKED_VENDOR_WEBGL / UNMASKED_RENDERER_WEBGL
 *
 * The raw GPU string leaks the real hardware (or "SwiftShader"/"Google
 * SwiftShader" under headless, an instant giveaway). We intercept
 * getParameter for the two unmasked constants on both WebGL1 and WebGL2 and
 * answer with the profile's string — which must match the OS claimed by the
 * UA (ANGLE/D3D on Windows, Metal/Apple GPU on macOS).
 */
export function makeWebglInitScript(): (profile: FingerprintProfile) => void {
  return profile => {
    // WEBGL_debug_renderer_info constants. Detectors fetch the extension and
    // then call getParameter(ext.UNMASKED_*), which lands here.
    const UNMASKED_VENDOR = 0x9245;
    const UNMASKED_RENDERER = 0x9246;

    const patch = (proto: object | undefined) => {
      if (!proto) return;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const anyProto = proto as any;
      const original: (pname: number) => unknown = anyProto.getParameter;
      if (typeof original !== 'function') return;
      const patched = function (this: unknown, pname: number) {
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
  };
}
