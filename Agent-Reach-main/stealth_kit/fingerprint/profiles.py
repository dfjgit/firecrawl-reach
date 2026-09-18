"""
Fingerprint profiles and the checkout/checkin pool.

A profile is the single source of truth for every fingerprint surface
(UA, UA-CH metadata, navigator.*, WebGL, headers, proxy). All fields must
be mutually consistent — `validator.py` enforces this before a context is
created, because detectors mostly catch *cross-surface contradictions*.

Port of stealth-kit/src/fingerprint/profiles.ts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union


@dataclass(frozen=True)
class UserAgentBrandVersion:
    brand: str
    version: str


@dataclass(frozen=True)
class UserAgentMetadata:
    """
    Mirrors CDP `Network.UserAgentMetadata` so it can be passed (via
    `to_cdp_dict()`) verbatim to Network.setUserAgentOverride.
    """

    brands: List[UserAgentBrandVersion]
    full_version_list: List[UserAgentBrandVersion]
    platform: str  # "Windows" | "macOS" | ...
    platform_version: str
    architecture: str
    bitness: str
    model: str
    mobile: bool
    full_version: str

    def to_cdp_dict(self) -> dict:
        """CamelCase wire shape expected by CDP and by the UA-CH init script."""
        return {
            "brands": [{"brand": b.brand, "version": b.version} for b in self.brands],
            "fullVersionList": [
                {"brand": b.brand, "version": b.version} for b in self.full_version_list
            ],
            "platform": self.platform,
            "platformVersion": self.platform_version,
            "architecture": self.architecture,
            "bitness": self.bitness,
            "model": self.model,
            "mobile": self.mobile,
            "fullVersion": self.full_version,
        }


@dataclass(frozen=True)
class Viewport:
    width: int
    height: int


@dataclass(frozen=True)
class FingerprintProfile:
    id: str
    """Pool filter labels, e.g. 'us-desktop', 'windows'."""
    tags: List[str]
    user_agent: str
    """sec-ch-ua / navigator.userAgentData source, in CDP wire shape."""
    user_agent_metadata: UserAgentMetadata
    viewport: Viewport
    device_scale_factor: float
    locale: str
    timezone_id: str
    """navigator.platform: 'Win32' | 'MacIntel' | 'Linux x86_64'"""
    platform: str
    """Value returned for WebGL UNMASKED_RENDERER_WEBGL."""
    webgl_renderer: str
    """Value returned for WebGL UNMASKED_VENDOR_WEBGL, e.g. 'Google Inc. (NVIDIA)'."""
    webgl_vendor: str
    """Fixed seed driving canvas noise — same profile must be reproducible."""
    canvas_noise_seed: int
    """Opaque key resolved to a Playwright proxy config by a user-supplied ProxyProvider."""
    proxy_id: str
    hardware_concurrency: int
    device_memory: int
    """Defaults to viewport when omitted."""
    screen: Optional[Viewport] = None


CHROME_148_BRANDS = [
    UserAgentBrandVersion("Not/A)Brand", "8"),
    UserAgentBrandVersion("Chromium", "148"),
    UserAgentBrandVersion("Google Chrome", "148"),
]

CHROME_148_FULL_VERSION_LIST = [
    UserAgentBrandVersion("Not/A)Brand", "8.0.0.0"),
    UserAgentBrandVersion("Chromium", "148.0.7778.96"),
    UserAgentBrandVersion("Google Chrome", "148.0.7778.96"),
]

# Built-in sample profiles.
#
# NOTE: these are *examples* to make the kit runnable out of the box.
# Production deployments should replace them with profiles harvested from
# real browsers (see DESIGN.md §3: real captures beat invented values).
BUILTIN_PROFILES: List[FingerprintProfile] = [
    FingerprintProfile(
        id="sample-win-chrome-desktop",
        tags=["us-desktop", "windows", "desktop"],
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        ),
        user_agent_metadata=UserAgentMetadata(
            brands=CHROME_148_BRANDS,
            full_version_list=CHROME_148_FULL_VERSION_LIST,
            platform="Windows",
            platform_version="15.0.0",
            architecture="x86",
            bitness="64",
            model="",
            mobile=False,
            full_version="148.0.7778.96",
        ),
        viewport=Viewport(1920, 1080),
        screen=Viewport(1920, 1080),
        device_scale_factor=1,
        locale="en-US",
        timezone_id="America/New_York",
        platform="Win32",
        webgl_renderer=(
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002503) "
            "Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
        webgl_vendor="Google Inc. (NVIDIA)",
        canvas_noise_seed=0x5EED0001,
        proxy_id="us-east-residential-01",
        hardware_concurrency=16,
        device_memory=8,
    ),
    FingerprintProfile(
        id="sample-mac-chrome-desktop",
        tags=["us-desktop", "mac", "desktop"],
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        ),
        user_agent_metadata=UserAgentMetadata(
            brands=CHROME_148_BRANDS,
            full_version_list=CHROME_148_FULL_VERSION_LIST,
            platform="macOS",
            platform_version="14.5.0",
            architecture="x86",
            bitness="64",
            model="",
            mobile=False,
            full_version="148.0.7778.96",
        ),
        viewport=Viewport(1440, 900),
        screen=Viewport(1440, 900),
        device_scale_factor=2,
        locale="en-US",
        timezone_id="America/Los_Angeles",
        platform="MacIntel",
        webgl_renderer="ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)",
        webgl_vendor="Google Inc. (Apple)",
        canvas_noise_seed=0x5EED0002,
        proxy_id="us-west-residential-01",
        hardware_concurrency=8,
        device_memory=8,
    ),
]


class ProfilePool:
    """
    Hands profiles out and takes them back so concurrent workers never share
    one identity. checkout(tag) filters by label; a checked-out profile is
    skipped until checkin returns it.
    """

    def __init__(self, profiles: Optional[List[FingerprintProfile]] = None):
        if profiles is None:
            profiles = BUILTIN_PROFILES
        if len(profiles) == 0:
            raise ValueError("ProfilePool requires at least one profile")
        self._profiles: List[FingerprintProfile] = list(profiles)
        self._in_use: Set[str] = set()

    def checkout(self, tag: Optional[str] = None) -> FingerprintProfile:
        candidates = [p for p in self._profiles if not tag or tag in p.tags]
        if len(candidates) == 0:
            raise LookupError(f'ProfilePool: no profile matches tag "{tag or "*"}"')
        free = next((p for p in candidates if p.id not in self._in_use), None)
        if free is None:
            raise LookupError(
                f'ProfilePool: all {len(candidates)} profile(s) matching '
                f'"{tag or "*"}" are checked out'
            )
        self._in_use.add(free.id)
        return free

    def checkin(self, profile: Union[FingerprintProfile, str]) -> None:
        pid = profile if isinstance(profile, str) else profile.id
        self._in_use.discard(pid)

    def find(self, pid: str) -> Optional[FingerprintProfile]:
        """Non-destructive lookup, does not mark the profile in use."""
        return next((p for p in self._profiles if p.id == pid), None)

    def list(self, tag: Optional[str] = None) -> List[FingerprintProfile]:
        return [p for p in self._profiles if not tag or tag in p.tags]

    @classmethod
    def load_json_dir(cls, path: Union[str, Path]) -> "ProfilePool":
        """
        Builds a pool from a directory of profile JSON files (as produced by
        detect/collect_profile.py). Every file is validated on load — a
        self-contradicting profile fails loudly instead of shipping.
        """
        directory = Path(path)
        if not directory.is_dir():
            raise FileNotFoundError(f"profile directory not found: {directory}")
        profiles = [
            load_profile_json(p)
            for p in sorted(directory.glob("*.json"))
        ]
        if not profiles:
            raise ValueError(f"no *.json profiles found in {directory}")
        return cls(profiles)


# ---------------------------------------------------------------------------
# JSON (de)serialization — used by detect/collect_profile.py output and by
# ProfilePool.load_json_dir. Schema: snake_case keys mirroring the dataclass
# field names.
# ---------------------------------------------------------------------------


def profile_to_dict(profile: FingerprintProfile) -> Dict[str, Any]:
    meta = profile.user_agent_metadata
    return {
        "id": profile.id,
        "tags": list(profile.tags),
        "user_agent": profile.user_agent,
        "user_agent_metadata": {
            "brands": [{"brand": b.brand, "version": b.version} for b in meta.brands],
            "full_version_list": [
                {"brand": b.brand, "version": b.version} for b in meta.full_version_list
            ],
            "platform": meta.platform,
            "platform_version": meta.platform_version,
            "architecture": meta.architecture,
            "bitness": meta.bitness,
            "model": meta.model,
            "mobile": meta.mobile,
            "full_version": meta.full_version,
        },
        "viewport": {"width": profile.viewport.width, "height": profile.viewport.height},
        "screen": (
            {"width": profile.screen.width, "height": profile.screen.height}
            if profile.screen
            else None
        ),
        "device_scale_factor": profile.device_scale_factor,
        "locale": profile.locale,
        "timezone_id": profile.timezone_id,
        "platform": profile.platform,
        "webgl_renderer": profile.webgl_renderer,
        "webgl_vendor": profile.webgl_vendor,
        "canvas_noise_seed": profile.canvas_noise_seed,
        "proxy_id": profile.proxy_id,
        "hardware_concurrency": profile.hardware_concurrency,
        "device_memory": profile.device_memory,
    }


def profile_from_dict(data: Dict[str, Any]) -> FingerprintProfile:
    m = data["user_agent_metadata"]
    meta = UserAgentMetadata(
        brands=[UserAgentBrandVersion(b["brand"], b["version"]) for b in m["brands"]],
        full_version_list=[
            UserAgentBrandVersion(b["brand"], b["version"])
            for b in m["full_version_list"]
        ],
        platform=m["platform"],
        platform_version=m["platform_version"],
        architecture=m["architecture"],
        bitness=m["bitness"],
        model=m["model"],
        mobile=m["mobile"],
        full_version=m["full_version"],
    )
    screen = data.get("screen")
    return FingerprintProfile(
        id=data["id"],
        tags=list(data.get("tags", [])),
        user_agent=data["user_agent"],
        user_agent_metadata=meta,
        viewport=Viewport(data["viewport"]["width"], data["viewport"]["height"]),
        screen=Viewport(screen["width"], screen["height"]) if screen else None,
        device_scale_factor=data["device_scale_factor"],
        locale=data["locale"],
        timezone_id=data["timezone_id"],
        platform=data["platform"],
        webgl_renderer=data["webgl_renderer"],
        webgl_vendor=data["webgl_vendor"],
        canvas_noise_seed=data["canvas_noise_seed"],
        proxy_id=data.get("proxy_id", ""),
        hardware_concurrency=data["hardware_concurrency"],
        device_memory=data["device_memory"],
    )


def load_profile_json(path: Union[str, Path]) -> FingerprintProfile:
    """Loads one profile JSON file and validates it."""
    from .validator import validate_profile

    with open(path, "r", encoding="utf-8") as f:
        profile = profile_from_dict(json.load(f))
    validate_profile(profile)
    return profile
