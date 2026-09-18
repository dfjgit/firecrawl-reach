"""
Proxy wiring.

TLS/JA3 NOTE — now implemented at the proxy layer.
Neither Playwright nor CDP can alter the browser's TLS ClientHello from
inside the library, so the JA3/JA4 rewrite happens in the bundled Go sidecar
(`sidecar/`, uTLS Chrome ClientHello) managed by `network/tls_proxy.py`.
This module remains for upstream/egress proxies: when a deployment needs a
residential exit IP in addition to the JA3 rewrite, point the egress proxy
at the local sidecar (or chain the sidecar upstream to the egress). The
profile↔proxy binding also matters for geography: timezone_id/locale of the
profile should match the exit IP's location (DESIGN.md §3). Verify the
rewrite with `detect/fingerprint_check.py --via-proxy`.

Port of stealth-kit/src/network/proxy.ts.
"""

from __future__ import annotations

from typing import Callable, Optional, TypedDict

from ..fingerprint.profiles import FingerprintProfile


class ProxyConfig(TypedDict, total=False):
    """Mirrors Playwright's inline proxy option shape."""

    server: str
    bypass: str
    username: str
    password: str


ProxyProvider = Callable[[str], Optional[ProxyConfig]]


def resolve_proxy(
    profile: FingerprintProfile, provider: Optional[ProxyProvider]
) -> Optional[ProxyConfig]:
    """Resolves the proxy for a profile through the user-supplied provider."""
    return provider(profile.proxy_id) if provider else None
