"""
Context factory: fingerprint-consistency assembly.

Creates a browser context whose every fingerprint surface is driven by one
validated profile:
  validate → context options → init scripts → CDP overrides → header routing.

Port of stealth-kit/src/core/context-factory.ts.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from playwright.sync_api import Browser, BrowserContext

from ..fingerprint.profiles import FingerprintProfile
from ..fingerprint.validator import validate_profile
from ..network.headers import setup_header_normalization
from ..network.proxy import ProxyProvider
from .align import align_profile_to_browser
from .cdp import attach_stealth_cdp
from .init_scripts import (
    make_canvas_init_script,
    make_navigator_props_init_script,
    make_permissions_init_script,
    make_plugins_init_script,
    make_webdriver_init_script,
    make_webgl_init_script,
)


def new_context(
    browser: Browser,
    profile: FingerprintProfile,
    proxy_provider: Optional[ProxyProvider] = None,
    overrides: Optional[Dict[str, Any]] = None,
    align: bool = True,
    tls_proxy: Optional[Any] = None,
) -> BrowserContext:
    """
    - profile: the fingerprint identity for the context.
    - proxy_provider: resolves profile.proxy_id to a Playwright proxy config.
      When omitted (or when it returns None) the context goes direct.
      Ignored when tls_proxy is in effect (the sidecar IS the proxy).
    - overrides: merged over the profile-derived context options; wins on
      conflict.
    - align: when True (default) and the browser came from stealth.launch(),
      the profile's UA / UA-CH brands are first aligned to the real browser
      family and version (core/align.py), so the JS and CDP surfaces agree
      with the brands the binary itself puts on the wire.
    - tls_proxy: a network.tls_proxy.TlsProxy routing this context through
      the uTLS sidecar (Chrome JA3/JA4 on the wire). When None, the sidecar
      auto-started by stealth.launch(ja3=True) is used if present. Because
      the sidecar terminates TLS with its own CA, this sets
      ignore_https_errors=True (overridable via overrides); install
      sidecar/ca/cert.pem into the system trust store to drop that.
    """
    from .launcher import browser_meta

    meta = browser_meta(browser)
    if tls_proxy is None and meta is not None:
        tls_proxy = meta[2]

    # Align to the real binary before anything else, so every surface below
    # (context UA, init scripts, CDP override, header values) uses it.
    if align and meta is not None:
        channel, browser_version = meta[0], meta[1]
        profile = align_profile_to_browser(
            profile, channel=channel, browser_version=browser_version
        )

    # Fail fast on a self-contradicting identity before touching the browser.
    validate_profile(profile)

    screen = profile.screen or profile.viewport
    context_options: Dict[str, Any] = {
        "user_agent": profile.user_agent,
        "viewport": {"width": profile.viewport.width, "height": profile.viewport.height},
        "screen": {"width": screen.width, "height": screen.height},
        "device_scale_factor": profile.device_scale_factor,
        "locale": profile.locale,
        "timezone_id": profile.timezone_id,
    }
    if tls_proxy is not None:
        context_options["proxy"] = dict(tls_proxy.proxy)
        # The sidecar MITMs TLS with its own CA (sidecar/ca/cert.pem).
        context_options["ignore_https_errors"] = True
    elif proxy_provider is not None:
        proxy = proxy_provider(profile.proxy_id)
        if proxy is not None:
            context_options["proxy"] = dict(proxy)
    context_options.update(overrides or {})

    context = browser.new_context(**context_options)

    # add_init_script runs in the main world via CDP
    # Page.addScriptToEvaluateOnNewDocument — no DOM/script-tag traces.
    context.add_init_script(make_webdriver_init_script())
    context.add_init_script(make_plugins_init_script())
    context.add_init_script(make_permissions_init_script())
    context.add_init_script(make_webgl_init_script(profile))
    context.add_init_script(make_canvas_init_script(profile))
    context.add_init_script(make_navigator_props_init_script(profile))

    attach_stealth_cdp(context, profile)
    setup_header_normalization(context, profile)

    return context
