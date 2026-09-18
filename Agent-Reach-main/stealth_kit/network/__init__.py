"""network subpackage — lazy so `import stealth_kit` works without playwright."""

from typing import Any

from .proxy import ProxyConfig, ProxyProvider, resolve_proxy
from .tls_proxy import TlsProxy, TlsProxyPool, UpstreamResolver

_LAZY_ATTRS = {
    "setup_header_normalization": (
        "stealth_kit.network.headers",
        "setup_header_normalization",
    ),
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
    "ProxyConfig",
    "ProxyProvider",
    "TlsProxy",
    "TlsProxyPool",
    "UpstreamResolver",
    "resolve_proxy",
    "setup_header_normalization",
]
