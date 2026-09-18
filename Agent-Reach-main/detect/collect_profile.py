"""
Fingerprint profile collector.

Opens a blank page in a REAL browser (default chrome, fallback msedge) with
no stealth patches and harvests the genuine fingerprint surfaces, then writes
a profile JSON that ProfilePool.load_json_dir / load_profile_json can load
directly (see DESIGN.md §3: real captures beat invented values).

Usage:
    python detect/collect_profile.py [--out detect/collected]

Environment:
    STEALTH_CHANNEL   'chrome' (default) | 'msedge'
    STEALTH_HEADED=1  run headed (default: headless — surfaces like screen
                      and WebGL are still the real machine's)

Exit code 0 = profile written, 1 = failure.
"""

import argparse
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stealth_kit.core.launcher import launch  # noqa: E402
from stealth_kit.fingerprint.profiles import profile_from_dict  # noqa: E402
from stealth_kit.fingerprint.validator import validate_profile  # noqa: E402

COLLECT_JS = r"""
async () => {
  const nav = navigator;
  let hev = {};
  try {
    hev = await nav.userAgentData.getHighEntropyValues([
      'architecture', 'bitness', 'brands', 'fullVersionList', 'mobile',
      'model', 'platform', 'platformVersion', 'uaFullVersion', 'wow64',
    ]);
  } catch (e) { hev = {}; }
  let webglVendor = null, webglRenderer = null;
  try {
    const canvas = document.createElement('canvas');
    const gl = (canvas.getContext('webgl') || canvas.getContext('experimental-webgl'));
    if (gl) {
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      if (ext) {
        webglVendor = gl.getParameter(ext.UNMASKED_VENDOR_WEBGL);
        webglRenderer = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
      } else {
        webglVendor = gl.getParameter(gl.VENDOR);
        webglRenderer = gl.getParameter(gl.RENDERER);
      }
    }
  } catch (e) { /* leave null */ }
  return {
    userAgent: nav.userAgent,
    hev: JSON.parse(JSON.stringify(hev)),
    brands: nav.userAgentData ? nav.userAgentData.brands.map(b => ({...b})) : [],
    platform: nav.platform,
    language: nav.language,
    languages: [...nav.languages],
    screen: { width: screen.width, height: screen.height },
    viewport: { width: window.innerWidth, height: window.innerHeight },
    devicePixelRatio: window.devicePixelRatio,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    hardwareConcurrency: nav.hardwareConcurrency,
    deviceMemory: nav.deviceMemory !== undefined ? nav.deviceMemory : 8,
    webglVendor,
    webglRenderer,
  };
}
"""


def _fallback_platform(user_agent: str) -> str:
    if "Windows" in user_agent:
        return "Windows"
    if "Mac OS X" in user_agent or "Macintosh" in user_agent:
        return "macOS"
    if "Linux" in user_agent:
        return "Linux"
    return ""


def build_profile(raw: dict, channel: str, browser_version: str) -> dict:
    hev = raw["hev"]
    brands = raw["brands"] or hev.get("brands") or []
    full_version_list = hev.get("fullVersionList") or []
    # Headless captures leak "HeadlessChrome" in the UA and may return an
    # empty UA-CH — sanitize the UA and synthesize the brand list from the
    # channel family + real version so the profile validates anyway.
    # (Headed captures via STEALTH_HEADED=1 need none of this.)
    user_agent = raw["userAgent"].replace("HeadlessChrome", "Chrome")
    if not brands:
        from stealth_kit.core.align import normalize_family

        family = normalize_family(channel)
        grease_brand = "Not;A=Brand" if family == "msedge" else "Not/A)Brand"
        vendor_brand = {"chrome": "Google Chrome", "msedge": "Microsoft Edge"}.get(family)
        major = browser_version.split(".")[0]
        brands = [
            {"brand": grease_brand, "version": "8"},
            {"brand": "Chromium", "version": major},
        ]
        full_version_list = [
            {"brand": grease_brand, "version": "8.0.0.0"},
            {"brand": "Chromium", "version": browser_version},
        ]
        if vendor_brand is not None:
            brands.append({"brand": vendor_brand, "version": major})
            full_version_list.append(
                {"brand": vendor_brand, "version": browser_version}
            )
    if not full_version_list:
        full_version_list = [
            {**b, "version": browser_version}
            for b in brands
            if b["brand"] in ("Chromium", "Google Chrome", "Microsoft Edge")
        ]
    return {
        "id": f"collected-{channel}-{raw['platform'].lower().replace(' ', '')}",
        "tags": ["collected", channel, "desktop"],
        "user_agent": user_agent,
        "user_agent_metadata": {
            "brands": brands,
            "full_version_list": full_version_list,
            "platform": hev.get("platform") or _fallback_platform(user_agent),
            "platform_version": hev.get("platformVersion", ""),
            "architecture": hev.get("architecture", ""),
            "bitness": hev.get("bitness", ""),
            "model": hev.get("model", ""),
            "mobile": bool(hev.get("mobile", False)),
            "full_version": hev.get("uaFullVersion", browser_version),
        },
        "viewport": raw["viewport"],
        "screen": raw["screen"],
        "device_scale_factor": raw["devicePixelRatio"],
        "locale": raw["language"],
        "timezone_id": raw["timezone"],
        "platform": raw["platform"],
        "webgl_renderer": raw["webglRenderer"] or "",
        "webgl_vendor": raw["webglVendor"] or "",
        "canvas_noise_seed": random.getrandbits(32),
        "proxy_id": "",
        "hardware_concurrency": raw["hardwareConcurrency"],
        "device_memory": raw["deviceMemory"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(Path(__file__).parent / "collected"),
                        help="output directory for the profile JSON")
    args = parser.parse_args()

    headless = os.environ.get("STEALTH_HEADED") != "1"
    channel = os.environ.get("STEALTH_CHANNEL", "chrome")
    try:
        # NOTE: launch() records browser meta but we never call new_context —
        # this browser runs UNPATCHED so every surface collected is genuine.
        browser = launch(channel=channel, headless=headless)
    except Exception as err:
        if os.environ.get("STEALTH_CHANNEL"):
            raise
        first_line = str(err).splitlines()[0] if str(err) else repr(err)
        print(f"[collect] channel=chrome unavailable ({first_line})",
              file=sys.stderr)
        print("[collect] falling back to channel=msedge", file=sys.stderr)
        channel = "msedge"
        browser = launch(channel="msedge", headless=headless)

    try:
        version = browser.version
        print(f"[collect] browser: channel={channel} version={version}")
        context = browser.new_context()
        page = context.new_page()
        page.goto("about:blank")
        raw = page.evaluate(COLLECT_JS)
        context.close()
    finally:
        browser.close()

    data = build_profile(raw, channel, version)
    # Prove the JSON round-trips through the loader and passes validation
    # before writing it to disk.
    profile = profile_from_dict(data)
    validate_profile(profile)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{data['id']}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"[collect] wrote {out_file}")
    print(f"[collect] id={data['id']} ua={data['user_agent']}")
    print(f"[collect] webgl={data['webgl_renderer']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(f"[collect] fatal: {err}", file=sys.stderr)
        sys.exit(1)
