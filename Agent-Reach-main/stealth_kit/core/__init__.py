"""core subpackage — lazy so `import stealth_kit` works without playwright."""

from typing import Any

from .align import align_profile_to_browser, expected_wire_brand, normalize_family

# name -> (module, attribute); these modules import playwright
_LAZY_ATTRS = {
    "launch": ("stealth_kit.core.launcher", "launch"),
    "session": ("stealth_kit.core.launcher", "session"),
    "browser_meta": ("stealth_kit.core.launcher", "browser_meta"),
    "EXTRA_ARGS": ("stealth_kit.core.launcher", "EXTRA_ARGS"),
    "STRIPPED_DEFAULT_ARGS": ("stealth_kit.core.launcher", "STRIPPED_DEFAULT_ARGS"),
    "new_context": ("stealth_kit.core.context_factory", "new_context"),
    "attach_stealth_cdp": ("stealth_kit.core.cdp", "attach_stealth_cdp"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(target[0]), target[1])
    globals()[name] = value
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY_ATTRS))


__all__ = [
    "EXTRA_ARGS",
    "STRIPPED_DEFAULT_ARGS",
    "align_profile_to_browser",
    "attach_stealth_cdp",
    "browser_meta",
    "expected_wire_brand",
    "launch",
    "new_context",
    "normalize_family",
    "session",
]
