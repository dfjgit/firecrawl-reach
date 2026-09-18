"""
Request header normalization via route interception.

Known limits (DESIGN.md §2.4):
 - Header *order* on the wire cannot be controlled through route.continue_;
   Chromium serializes headers itself. Targets that fingerprint header
   order or HTTP/2 frames need the proxy layer (see network/proxy.py).
 - Some forbidden headers are passed through untouched by Chromium.

Port of stealth-kit/src/network/headers.ts.
"""

from __future__ import annotations

from playwright.sync_api import BrowserContext

from ..fingerprint.profiles import FingerprintProfile


def _sec_ch_ua(profile: FingerprintProfile) -> str:
    return ", ".join(
        f'"{b.brand}";v="{b.version}"' for b in profile.user_agent_metadata.brands
    )


def setup_header_normalization(context: BrowserContext, profile: FingerprintProfile) -> None:
    meta = profile.user_agent_metadata
    base_lang = profile.locale.split("-")[0]
    accept_language = f"{profile.locale},{base_lang};q=0.9"
    sec_ch_ua = _sec_ch_ua(profile)
    sec_ch_ua_platform = f'"{meta.platform}"'
    sec_ch_ua_mobile = "?1" if meta.mobile else "?0"

    def handler(route) -> None:
        headers = {
            **route.request.headers,
            "sec-ch-ua": sec_ch_ua,
            "sec-ch-ua-platform": sec_ch_ua_platform,
            "sec-ch-ua-mobile": sec_ch_ua_mobile,
            "accept-language": accept_language,
        }
        route.continue_(headers=headers)

    context.route("**/*", handler)
