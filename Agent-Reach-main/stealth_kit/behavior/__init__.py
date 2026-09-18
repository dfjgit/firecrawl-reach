"""behavior subpackage — lazy so `import stealth_kit` works without playwright."""

from typing import Any

_LAZY_ATTRS = {
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

    value = getattr(importlib.import_module(target[0]), target[1])
    globals()[name] = value
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY_ATTRS))


__all__ = ["human_click", "human_scroll", "human_type", "move_mouse"]
