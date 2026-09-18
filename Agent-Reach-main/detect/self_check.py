"""
Self-check: launches a stealthed browser, collects the fingerprint
surfaces this kit patches, and asserts they match the profile — plus a
behavior-layer group (mouse/typing/click dynamics).

Usage:
    python detect/self_check.py

Environment:
    STEALTH_CHANNEL       'chrome' (default) | 'msedge' | 'chromium'
                          'chromium' = bundled build via executable-path scan
                          of the local ms-playwright cache (no download).
                          When unset, falls back chrome -> msedge -> chromium.
    STEALTH_HEADED=1      run headed (default: headless)
    STEALTH_PROFILE_JSON  path to a profile JSON file or a directory of them
                          (as produced by collect_profile.py); defaults to the
                          first built-in sample profile.

Exit code 0 = all checks passed, 1 = at least one failed.

Port of stealth-kit/detect/self-check.ts, extended with brand-alignment and
behavior checks.
"""

import json
import os
import random
import re
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stealth_kit import BUILTIN_PROFILES, human, stealth  # noqa: E402
from stealth_kit.core.align import align_profile_to_browser, normalize_family  # noqa: E402
from stealth_kit.fingerprint.profiles import load_profile_json  # noqa: E402


def load_profile():
    """Profile under test: a collected JSON when STEALTH_PROFILE_JSON is set,
    else the first built-in sample."""
    spec = os.environ.get("STEALTH_PROFILE_JSON")
    if not spec:
        return BUILTIN_PROFILES[0]
    path = Path(spec)
    if path.is_dir():
        files = sorted(path.glob("*.json"))
        if not files:
            raise RuntimeError(f"STEALTH_PROFILE_JSON: no *.json in {path}")
        path = files[0]
    return load_profile_json(path)


def _ms_playwright_cache() -> Path:
    """The driver-default browser cache, per OS (PLAYWRIGHT_BROWSERS_PATH wins)."""
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env:
        return Path(env)
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


# Executable path inside a chromium-<rev> build dir, per OS.
_CHROMIUM_EXE_CANDIDATES = {
    "win32": ("chrome-win64/chrome.exe", "chrome-win/chrome.exe"),
    "darwin": (
        "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        "chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium",
        "chrome-mac-x64/Chromium.app/Contents/MacOS/Chromium",
    ),
    "linux": ("chrome-linux/chrome",),
}


def find_bundled_chromium() -> Optional[str]:
    cache = _ms_playwright_cache()
    if not cache.is_dir():
        return None
    candidates = _CHROMIUM_EXE_CANDIDATES.get(
        sys.platform, _CHROMIUM_EXE_CANDIDATES["linux"]
    )
    # Prefer full Chromium over headless_shell; newest revision first.
    dirs = sorted(
        (d for d in os.listdir(cache) if re.fullmatch(r"chromium-\d+", d)),
        reverse=True,
    )
    for d in dirs:
        for rel in candidates:
            exe = cache / d / rel
            if exe.is_file():
                return str(exe)
    return None


def launch_browser():
    """Returns (browser, description, channel_actually_used)."""
    headless = os.environ.get("STEALTH_HEADED") != "1"
    channel = os.environ.get("STEALTH_CHANNEL", "chrome")

    if channel == "chromium":
        executable_path = find_bundled_chromium()
        if not executable_path:
            raise RuntimeError(
                "STEALTH_CHANNEL=chromium but no bundled Chromium found in "
                "ms-playwright cache"
            )
        browser = stealth.launch(channel=None, headless=headless,
                                 executable_path=executable_path)
        return browser, f"bundled chromium ({executable_path})", None

    try:
        browser = stealth.launch(channel=channel, headless=headless)
        return browser, f"channel={channel}", channel
    except Exception as err:
        if os.environ.get("STEALTH_CHANNEL"):
            raise  # explicit choice: don't silently fall back
        first_line = str(err).splitlines()[0] if str(err) else repr(err)
        print(f"[self-check] channel=chrome unavailable ({first_line})",
              file=sys.stderr)
        print("[self-check] falling back to channel=msedge", file=sys.stderr)
        try:
            browser = stealth.launch(channel="msedge", headless=headless)
            return browser, "channel=msedge", "msedge"
        except Exception as err2:
            first_line = str(err2).splitlines()[0] if str(err2) else repr(err2)
            print(f"[self-check] channel=msedge unavailable ({first_line})",
                  file=sys.stderr)
            executable_path = find_bundled_chromium()
            if not executable_path:
                raise RuntimeError(
                    "no usable browser: chrome/msedge channels failed and no "
                    "bundled Chromium in ms-playwright cache"
                ) from err2
            print("[self-check] falling back to bundled chromium",
                  file=sys.stderr)
            browser = stealth.launch(channel=None, headless=headless,
                                     executable_path=executable_path)
            return browser, f"bundled chromium ({executable_path})", None


COLLECT_JS = r"""
() => {
  let webglRenderer = null;
  try {
    const canvas = document.createElement('canvas');
    const gl = (canvas.getContext('webgl') || canvas.getContext('experimental-webgl'));
    if (gl) {
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      webglRenderer = ext
        ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)
        : gl.getParameter(gl.RENDERER);
    }
  } catch (e) {
    webglRenderer = null;
  }
  const nav = navigator;
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
"""

# Behavior test page: records mouse/key dynamics into window globals.
BEHAVIOR_HTML = """
<html><body style="height:3000px">
<button id="b" style="width:120px;height:48px">B</button>
<input id="i" type="text"><input id="j" type="text">
<script>
window._mm = []; window._kd1 = []; window._kd2 = []; window._ev = [];
document.addEventListener('mousemove', e => {
  window._mm.push([e.clientX, e.clientY, performance.now()]);
});
document.getElementById('i').addEventListener('keydown', () => {
  window._kd1.push(performance.now());
});
document.getElementById('j').addEventListener('keydown', () => {
  window._kd2.push(performance.now());
});
for (const t of ['mouseover', 'mousemove', 'mousedown', 'mouseup']) {
  document.getElementById('b').addEventListener(t, () => window._ev.push(t));
}
</script>
</body></html>
"""


def _diffs(values: List[float]) -> List[float]:
    return [values[i + 1] - values[i] for i in range(len(values) - 1)]


def _variance(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def main() -> None:
    profile = load_profile()
    browser, description, channel_used = launch_browser()
    family = normalize_family(channel_used)
    # What new_context(align=True) will make the profile look like; all
    # assertions compare against this aligned identity.
    expected = align_profile_to_browser(
        profile, channel=channel_used, browser_version=browser.version
    )
    browser_major = browser.version.split(".")[0]
    vendor_brand = {
        "msedge": "Microsoft Edge",
        "chrome": "Google Chrome",
        "chromium": "Chromium",
    }[family]
    print(f"[self-check] browser: {description} (version {browser.version}), "
          f"profile: {profile.id}")

    failed = 0

    def check(name: str, ok: bool, actual) -> None:
        nonlocal failed
        suffix = "" if ok else f"  (got: {json.dumps(actual, default=str)})"
        print(f"{'PASS' if ok else 'FAIL'}  {name}{suffix}")
        if not ok:
            failed += 1

    try:
        context = stealth.new_context(browser, profile=profile)
        page = context.new_page()
        page.set_content("<html><body><p>stealth self-check</p></body></html>")
        # Give the CDP attach a beat to land before reading UA surfaces.
        page.wait_for_timeout(300)

        c = page.evaluate(COLLECT_JS)

        check("navigator.webdriver === undefined",
              c["webdriver"] is None, c["webdriver"])
        check("navigator.platform matches profile",
              c["platform"] == expected.platform, c["platform"])
        check("UA has no HeadlessChrome",
              "HeadlessChrome" not in c["userAgent"], c["userAgent"])
        check("UA matches (aligned) profile",
              c["userAgent"] == expected.user_agent, c["userAgent"])
        check("navigator.plugins non-empty",
              c["pluginsLength"] > 0, c["pluginsLength"])
        check("navigator.languages matches locale",
              c["languages"][0] == expected.locale
              and expected.locale.split("-")[0] in c["languages"],
              c["languages"])
        check("WebGL renderer matches profile",
              c["webglRenderer"] == expected.webgl_renderer, c["webglRenderer"])
        check("userAgentData brands present",
              bool(c["uaDataBrands"])
              and any(b["brand"] == "Chromium" for b in c["uaDataBrands"]),
              c["uaDataBrands"])
        check("userAgentData aligned to real browser brands",
              bool(c["uaDataBrands"])
              and any(b["brand"] == vendor_brand
                      and b["version"] == browser_major
                      for b in c["uaDataBrands"]),
              c["uaDataBrands"])
        check("timezone matches profile",
              c["timezone"] == expected.timezone_id, c["timezone"])
        check("hardwareConcurrency matches profile",
              c["hardwareConcurrency"] == expected.hardware_concurrency,
              c["hardwareConcurrency"])
        check("deviceMemory matches profile",
              c["deviceMemory"] == expected.device_memory, c["deviceMemory"])

        # ---------------- behavior group ----------------
        bpage = context.new_page()
        bpage.set_content(BEHAVIOR_HTML)

        human.move_mouse(bpage, 700, 500, rng=random.Random(123))
        mm = bpage.evaluate("window._mm")
        check("behavior: move_mouse fires >=10 mousemove events",
              len(mm) >= 10, len(mm))
        dx = _diffs([p[0] for p in mm])
        dt = _diffs([p[2] for p in mm])
        check("behavior: mouse path is not an equidistant straight line",
              _variance(dx) > 0.01, [round(v, 2) for v in dx[:6]])
        check("behavior: mouse event intervals vary (not a metronome)",
              _variance(dt) > 0.01, [round(v, 2) for v in dt[:6]])

        human.type(bpage, "#i", "hello world", rng=random.Random(42))
        human.type(bpage, "#j", "hello world", rng=random.Random(42))
        kd1 = bpage.evaluate("window._kd1")
        kd2 = bpage.evaluate("window._kd2")
        i1 = _diffs(kd1)
        i2 = _diffs(kd2)
        check("behavior: typing produced key events for every character",
              len(kd1) >= len("hello world") and len(kd1) == len(kd2),
              [len(kd1), len(kd2)])
        check("behavior: typing interval variance > 0 (log-normal)",
              _variance(i1) > 0, round(_variance(i1), 2))
        # Same seed -> same delay draws. Measured browser timestamps carry a
        # few ms of OS scheduling jitter, so determinism is asserted with a
        # per-interval tolerance rather than exact float equality.
        max_gap = max((abs(a - b) for a, b in zip(i1, i2)), default=None)
        check("behavior: seeded typing runs are deterministic (+/-40ms)",
              len(i1) == len(i2) and max_gap is not None and max_gap <= 40,
              None if max_gap is None else round(max_gap, 2))

        bpage.evaluate("window._ev = []")
        human.click(bpage, "#b", rng=random.Random(7))
        ev = bpage.evaluate("window._ev")
        first_down = ev.index("mousedown") if "mousedown" in ev else -1
        check("behavior: click is preceded by hover on the target",
              first_down > 0
              and any(e in ("mouseover", "mousemove") for e in ev[:first_down]),
              ev[:6])

        context.close()
    finally:
        browser.close()

    print("\nALL CHECKS PASSED" if failed == 0 else f"\n{failed} CHECK(S) FAILED")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(f"[self-check] fatal: {err}", file=sys.stderr)
        sys.exit(1)
