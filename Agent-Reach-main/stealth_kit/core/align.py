"""
Profile ↔ real-browser brand alignment.

A profile that claims "Chrome 148" while the actual binary is Edge 150 is a
cross-surface contradiction: the wire-level sec-ch-ua brands (which Chromium
generates from its own brand list and which neither route interception nor
CDP can rewrite — see DESIGN.md §2.4) expose the real browser family and
major version. Detectors diff that against the UA string and
navigator.userAgentData.

This module rewrites a profile's UA-CH surfaces to match the browser that
was actually launched, so every surface we *can* control (UA string, JS
userAgentData, CDP metadata, header-normalization values) agrees with the
one surface we cannot (the browser's own brand list on the wire).

The original profile object is never mutated; an aligned copy is returned.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Optional, Tuple

from ..fingerprint.profiles import (
    FingerprintProfile,
    UserAgentBrandVersion,
)

# brand family -> (GREASE placeholder, vendor brand or None for plain Chromium)
_FAMILIES = {
    "chrome": ("Not/A)Brand", "Google Chrome"),
    "msedge": ("Not;A=Brand", "Microsoft Edge"),
    "chromium": ("Not/A)Brand", None),
}

# Real browsers ship the GREASE brand at major version 8.
_GREASE_VERSION = "8"

_CHROME_TOKEN_RE = re.compile(r"Chrome/\d+\.\d+\.\d+\.\d+")
_EDG_TOKEN_RE = re.compile(r"\s+Edg/[\d.]+")


def normalize_family(channel: Optional[str]) -> str:
    """Maps a Playwright channel (or None = bundled build) to a brand family."""
    if not channel:
        return "chromium"
    lowered = channel.lower()
    if "edg" in lowered:
        return "msedge"
    if lowered == "chrome" or lowered.startswith("chrome-"):
        return "chrome"
    return "chromium"


def _align_ua(user_agent: str, family: str, major: str, full_version: str) -> str:
    ua = _CHROME_TOKEN_RE.sub(f"Chrome/{major}.0.0.0", user_agent)
    ua = _EDG_TOKEN_RE.sub("", ua)
    if family == "msedge":
        ua = f"{ua} Edg/{full_version}"
    return ua


def align_profile_to_browser(
    profile: FingerprintProfile,
    *,
    channel: Optional[str],
    browser_version: str,
) -> FingerprintProfile:
    """
    Returns a copy of `profile` whose UA string and UA-CH metadata match the
    brand family and major version of the actually-launched browser.

    - channel: the Playwright channel the browser was launched with
      ('chrome' | 'msedge' | None for bundled Chromium).
    - browser_version: `browser.version`, e.g. '150.0.3538.71'.
    """
    family = normalize_family(channel)
    grease_brand, vendor_brand = _FAMILIES[family]
    major = browser_version.split(".")[0]

    brands = [
        UserAgentBrandVersion(grease_brand, _GREASE_VERSION),
        UserAgentBrandVersion("Chromium", major),
    ]
    full_version_list = [
        UserAgentBrandVersion(grease_brand, f"{_GREASE_VERSION}.0.0.0"),
        UserAgentBrandVersion("Chromium", browser_version),
    ]
    if vendor_brand is not None:
        brands.append(UserAgentBrandVersion(vendor_brand, major))
        full_version_list.append(UserAgentBrandVersion(vendor_brand, browser_version))

    meta = dataclasses.replace(
        profile.user_agent_metadata,
        brands=brands,
        full_version_list=full_version_list,
        full_version=browser_version,
    )
    return dataclasses.replace(
        profile,
        user_agent=_align_ua(profile.user_agent, family, major, browser_version),
        user_agent_metadata=meta,
    )


def expected_wire_brand(channel: Optional[str]) -> Tuple[str, ...]:
    """The vendor brands the real binary puts on the wire, for diagnostics."""
    family = normalize_family(channel)
    _, vendor_brand = _FAMILIES[family]
    return ("Chromium",) + ((vendor_brand,) if vendor_brand else ())
