"""
Launcher: channel / args / ignore_default_args strategy.

Port of stealth-kit/src/core/launcher.ts to the Playwright Python sync API.
`launch()` manages the `sync_playwright()` lifecycle internally: the driver
is started on demand, stopped when the returned browser is closed (and via an
atexit hook as a safety net). A `session()` context manager is provided for
callers who prefer explicit scoping.
"""

from __future__ import annotations

import atexit
import contextlib
import weakref
from typing import Any, Iterator, List, Optional, Tuple

from playwright.sync_api import Browser, sync_playwright

from ..network.proxy import ProxyConfig

# Default-arg switches Playwright adds that we deliberately strip.
#
# `ignore_default_args` is an EXACT string match against Playwright's internal
# switch list (chromiumSwitches.ts), so every entry here must be verified
# against the installed Playwright version on upgrade — that is why this
# list is an explicit constant with per-entry rationale, not inline config.
STRIPPED_DEFAULT_ARGS: List[str] = [
    # Real users have extensions; a browser that cannot load any stands out.
    # (Also removes the need for --disable-component-extensions-with-background-pages.)
    "--disable-extensions",
    "--disable-component-extensions-with-background-pages",
    # Disabling popup blocking changes window.open behavior in detectable ways.
    "--disable-popup-blocking",
]

# Extra args appended on top of Playwright's defaults.
EXTRA_ARGS: List[str] = [
    # Belt and braces: Playwright does not pass this today, but if a future
    # version (or a channel build) ever does, automation-controlled Blink
    # features stay off.
    "--disable-blink-features=AutomationControlled",
]

# Live sync_playwright() driver instances started by launch().
_playwrights: List[Any] = []

# (channel, browser_version, tls_proxy) recorded for every browser started
# by launch(), so new_context() can align profiles to the real binary
# (see core/align.py) and pick up an auto-started sidecar (see ja3=...).
_browser_meta: "weakref.WeakKeyDictionary[Browser, Tuple[Optional[str], str, Any]]" = (
    weakref.WeakKeyDictionary()
)


def browser_meta(
    browser: Browser,
) -> "Optional[Tuple[Optional[str], str, Any]]":
    """Returns (channel, version, tls_proxy) if from stealth.launch()."""
    return _browser_meta.get(browser)


def _stop_playwright(pw: Any) -> None:
    if pw in _playwrights:
        _playwrights.remove(pw)
    try:
        pw.stop()
    except Exception:
        pass


def _stop_all() -> None:
    for pw in list(_playwrights):
        _stop_playwright(pw)


atexit.register(_stop_all)


def launch(
    channel: Optional[str] = "chrome",
    headless: bool = False,
    args: Optional[List[str]] = None,
    ignore_default_args: Optional[List[str]] = None,
    proxy: Optional[ProxyConfig] = None,
    ja3: bool = False,
    upstream: Optional[str] = None,
    **extra_options: Any,
) -> Browser:
    """
    Launch a stealthed Chromium.

    - channel: defaults to 'chrome' — driving the real installed Chrome binary
      sidesteps the binary-level differences of bundled Chromium
      (DESIGN.md §0.3). Pass 'msedge', or None for bundled Chromium.
    - headless: defaults to False — headed browsers look far less automated.
    - ja3: when True, auto-start the Go uTLS sidecar (network/tls_proxy.py)
      and route this browser's stealth contexts through it, so upstream
      peers see a Chrome TLS ClientHello (JA3/JA4) instead of the real
      browser's. The sidecar stops when the browser closes. Requires
      sidecar/bin/tlsproxy (tlsproxy.exe on Windows; built automatically
      when `go` is on PATH).
    - upstream: optional egress proxy URL (http:// or socks5://, optional
      user:pass@) the sidecar chains through — target sites then see the
      egress IP with the Chrome fingerprint. Only meaningful with ja3=True.
    - extra_options: escape hatch for any other playwright launch option
      (e.g. executable_path=...).

    The underlying sync_playwright() driver stops automatically when the
    returned browser is closed, or at interpreter exit.
    """
    pw = sync_playwright().start()
    _playwrights.append(pw)

    launch_kwargs: dict = {
        "channel": channel,
        "headless": headless,
        "args": [*EXTRA_ARGS, *(args or [])],
        "ignore_default_args": (
            list(STRIPPED_DEFAULT_ARGS) if ignore_default_args is None else ignore_default_args
        ),
        **extra_options,
    }
    if proxy is not None:
        launch_kwargs["proxy"] = dict(proxy)

    tls_proxy = None
    try:
        browser = pw.chromium.launch(**launch_kwargs)
        if ja3:
            from ..network.tls_proxy import TlsProxy

            tls_proxy = TlsProxy(upstream=upstream)
    except Exception:
        _stop_playwright(pw)
        raise

    _browser_meta[browser] = (channel, browser.version, tls_proxy)

    # Tie the driver lifetime to the browser: closing the browser also stops
    # sync_playwright(), so callers can't leak the driver process.
    original_close = browser.close

    def close_and_stop(*c_args: Any, **c_kwargs: Any) -> None:
        try:
            original_close(*c_args, **c_kwargs)
        finally:
            if tls_proxy is not None:
                tls_proxy.stop()
            _stop_playwright(pw)

    try:
        browser.close = close_and_stop  # type: ignore[method-assign]
    except AttributeError:
        # Slots-based object — fall back to the atexit hook only.
        pass

    return browser


@contextlib.contextmanager
def session(**kwargs: Any) -> Iterator[Browser]:
    """Context manager form of launch(): closes browser + driver on exit."""
    browser = launch(**kwargs)
    try:
        yield browser
    finally:
        browser.close()
