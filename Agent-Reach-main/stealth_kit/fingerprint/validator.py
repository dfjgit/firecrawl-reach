"""
Cross-consistency checks run before any context is created. A profile whose
surfaces contradict each other (Mac UA + Win32 platform, UA version ≠
UA-CH version, ...) is the single most common way detectors catch stealth
setups, so we fail hard instead of shipping a broken identity.

Port of stealth-kit/src/fingerprint/validator.ts — same rules, same messages.
"""

from __future__ import annotations

import re
import warnings
from typing import List, Optional

from .profiles import FingerprintProfile


class ProfileValidationError(ValueError):
    def __init__(self, profile_id: str, problems: List[str]):
        self.profile_id = profile_id
        self.problems = problems
        super().__init__(
            f'Invalid fingerprint profile "{profile_id}":\n  - ' + "\n  - ".join(problems)
        )


def _chrome_major_version(user_agent: str) -> Optional[str]:
    # Real desktop Chrome freezes the tail as "Chrome/<major>.0.0.0".
    m = re.search(r"Chrome/(\d+)\.\d+\.\d+\.\d+", user_agent)
    return m.group(1) if m else None


def collect_warnings(
    profile: FingerprintProfile, browser_version: Optional[str] = None
) -> List[str]:
    """
    Soft (non-fatal) consistency warnings. Unlike validate_profile, these
    never raise — they flag situations that are survivable but suspicious.
    """
    found: List[str] = []
    if browser_version:
        ua_major = _chrome_major_version(profile.user_agent)
        browser_major = browser_version.split(".")[0]
        if ua_major and ua_major != browser_major:
            found.append(
                f'profile "{profile.id}" claims Chrome major {ua_major} but the '
                f"browser binary is {browser_major} — run align_profile_to_browser() "
                "or leave new_context(align=True) on"
            )
    return found


def validate_profile(
    profile: FingerprintProfile, browser_version: Optional[str] = None
) -> None:
    for warning in collect_warnings(profile, browser_version):
        warnings.warn(f"[stealth-kit] {warning}", stacklevel=2)
    problems: List[str] = []
    ua = profile.user_agent
    meta = profile.user_agent_metadata

    if "HeadlessChrome" in ua:
        problems.append('userAgent must not contain "HeadlessChrome"')

    # --- UA platform vs navigator.platform ---
    if "Windows" in ua:
        if profile.platform != "Win32":
            problems.append(
                f'UA says Windows but platform is "{profile.platform}" (expected "Win32")'
            )
        if meta.platform != "Windows":
            problems.append(
                f'UA says Windows but userAgentMetadata.platform is "{meta.platform}"'
            )
    elif "Mac OS X" in ua or "Macintosh" in ua:
        if profile.platform != "MacIntel":
            problems.append(
                f'UA says macOS but platform is "{profile.platform}" (expected "MacIntel")'
            )
        if meta.platform != "macOS":
            problems.append(
                f'UA says macOS but userAgentMetadata.platform is "{meta.platform}"'
            )
    elif "Linux" in ua:
        if not profile.platform.startswith("Linux"):
            problems.append(f'UA says Linux but platform is "{profile.platform}"')
        if meta.platform != "Linux":
            problems.append(
                f'UA says Linux but userAgentMetadata.platform is "{meta.platform}"'
            )
    else:
        problems.append(
            "userAgent platform token not recognized (expected Windows / Mac OS X / Linux)"
        )

    # --- UA Chrome major version vs UA-CH brands ---
    ua_major = _chrome_major_version(ua)
    if not ua_major:
        problems.append("could not parse Chrome version from userAgent")
    else:
        brand_entry = next(
            (b for b in meta.brands if b.brand in ("Chromium", "Google Chrome")), None
        )
        if brand_entry is None:
            problems.append("userAgentMetadata.brands has no Chromium/Google Chrome entry")
        elif brand_entry.version != ua_major:
            problems.append(
                f'UA Chrome major version {ua_major} != brands["{brand_entry.brand}"] '
                f"= {brand_entry.version}"
            )
        full_entry = next(
            (b for b in meta.full_version_list if b.brand in ("Chromium", "Google Chrome")),
            None,
        )
        if full_entry is None:
            problems.append(
                "userAgentMetadata.fullVersionList has no Chromium/Google Chrome entry"
            )
        elif not full_entry.version.startswith(f"{ua_major}."):
            problems.append(
                f'UA Chrome major version {ua_major} != fullVersionList["{full_entry.brand}"] '
                f"= {full_entry.version}"
            )
        if not meta.full_version.startswith(f"{ua_major}."):
            problems.append(
                f'userAgentMetadata.fullVersion "{meta.full_version}" != UA major {ua_major}'
            )

    # --- Geometry sanity ---
    viewport = profile.viewport
    screen = profile.screen

    def in_range(n: float) -> bool:
        return 320 <= n <= 7680

    if not in_range(viewport.width) or not in_range(viewport.height):
        problems.append(
            f"viewport {viewport.width}x{viewport.height} is outside a plausible range"
        )
    if screen is not None:
        if not in_range(screen.width) or not in_range(screen.height):
            problems.append(
                f"screen {screen.width}x{screen.height} is outside a plausible range"
            )
        if screen.width < viewport.width or screen.height < viewport.height:
            problems.append(
                f"screen {screen.width}x{screen.height} is smaller than viewport "
                f"{viewport.width}x{viewport.height}"
            )

    if problems:
        raise ProfileValidationError(profile.id, problems)
