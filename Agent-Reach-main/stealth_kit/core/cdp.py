"""
CDP enhancement: UA override, Emulation.

Protocol-level overrides leave no JS-visible patching traces (no
defineProperty footprints), so prefer them over init-script hacks for
anything CDP can express.

Port of stealth-kit/src/core/cdp.ts.
"""

from __future__ import annotations

import sys

from playwright.sync_api import BrowserContext, Page

from ..fingerprint.profiles import FingerprintProfile


def _apply_to_page(page: Page, profile: FingerprintProfile) -> None:
    try:
        session = page.context.new_cdp_session(page)
        session.send(
            "Network.setUserAgentOverride",
            {
                "userAgent": profile.user_agent,
                "acceptLanguage": profile.locale,
                # Keeps the sec-ch-ua-* request headers consistent with the UA
                # and with the navigator.userAgentData init script.
                "userAgentMetadata": profile.user_agent_metadata.to_cdp_dict(),
            },
        )
        session.send(
            "Emulation.setTimezoneOverride",
            {"timezoneId": profile.timezone_id},
        )
    except Exception as err:
        # A page that crashes or closes mid-attach should not kill the context.
        print(f"[stealth-kit] CDP attach failed for page: {err}", file=sys.stderr)


def attach_stealth_cdp(context: BrowserContext, profile: FingerprintProfile) -> None:
    """
    Applies CDP-level UA/UA-CH/timezone overrides to every page in the
    context — existing ones and any opened later.
    """
    for page in context.pages:
        _apply_to_page(page, profile)
    context.on("page", lambda page: _apply_to_page(page, profile))
