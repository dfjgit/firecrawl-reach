"""
stealth-kit — anti-detect (stealth) wrapper around Playwright, Python port.

Usage mirrors the TypeScript version:

    from stealth_kit import stealth, human, ProfilePool

    pool = ProfilePool()
    with stealth.session(channel="msedge", headless=True) as browser:
        context = stealth.new_context(browser, profile=pool.checkout("us-desktop"))
        page = context.new_page()
        page.goto("https://example.com")
        human.click(page, "text=Submit")

All capabilities use only public Playwright APIs: add_init_script,
new_cdp_session, launch(channel/args/ignore_default_args), context.route,
new_context options. See DESIGN.md in the TypeScript stealth-kit for the
full rationale.

Playwright is an OPTIONAL dependency: importing this package (and using the
fingerprint profiles, validator, profile pool, brand alignment or TlsProxy
process manager) works without it. Only the browser-facing entry points
(stealth.*, human.*, SessionPool) import playwright, lazily, on first use.
"""

from typing import TYPE_CHECKING, Any

# -- eager: no playwright required ----------------------------------------
from .core.align import align_profile_to_browser
from .fingerprint.profiles import (
    BUILTIN_PROFILES,
    FingerprintProfile,
    ProfilePool,
    UserAgentBrandVersion,
    UserAgentMetadata,
    Viewport,
    load_profile_json,
    profile_from_dict,
    profile_to_dict,
)
from .fingerprint.validator import (
    ProfileValidationError,
    collect_warnings,
    validate_profile,
)
from .network.proxy import ProxyConfig, ProxyProvider
from .network.tls_proxy import TlsProxy, TlsProxyPool, UpstreamResolver

# -- lazy: import playwright on first access -------------------------------
# name -> (module, attribute)
_LAZY_ATTRS = {
    "stealth": ("stealth_kit._namespaces", "stealth"),
    "human": ("stealth_kit._namespaces", "human"),
    "launch": ("stealth_kit.core.launcher", "launch"),
    "session": ("stealth_kit.core.launcher", "session"),
    "new_context": ("stealth_kit.core.context_factory", "new_context"),
    "SessionPool": ("stealth_kit.session_pool", "SessionPool"),
    "human_click": ("stealth_kit.behavior.mouse", "human_click"),
    "move_mouse": ("stealth_kit.behavior.mouse", "move_mouse"),
    "human_type": ("stealth_kit.behavior.typing", "human_type"),
    "human_scroll": ("stealth_kit.behavior.scroll", "human_scroll"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(target[0])
    value = getattr(module, target[1])
    globals()[name] = value  # cache: subsequent lookups skip __getattr__
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY_ATTRS))


if TYPE_CHECKING:  # static view for mypy/IDEs
    from ._namespaces import human, stealth
    from .behavior.mouse import human_click, move_mouse
    from .behavior.scroll import human_scroll
    from .behavior.typing import human_type
    from .core.context_factory import new_context
    from .core.launcher import launch, session
    from .session_pool import SessionPool

__all__ = [
    "BUILTIN_PROFILES",
    "FingerprintProfile",
    "ProfilePool",
    "ProfileValidationError",
    "ProxyConfig",
    "ProxyProvider",
    "SessionPool",
    "TlsProxy",
    "TlsProxyPool",
    "UpstreamResolver",
    "UserAgentBrandVersion",
    "UserAgentMetadata",
    "Viewport",
    "align_profile_to_browser",
    "collect_warnings",
    "human",
    "human_click",
    "human_scroll",
    "human_type",
    "launch",
    "load_profile_json",
    "move_mouse",
    "new_context",
    "profile_from_dict",
    "profile_to_dict",
    "session",
    "stealth",
    "validate_profile",
]
