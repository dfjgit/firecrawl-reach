"""
Pooled browser contexts with fingerprint ↔ session binding.

One profile identity maps to one long-lived context: cookies, storage and
the fingerprint travel together, and concurrent workers never accidentally
mint a second context for the same identity (which would look like one
"user" logging in from two browsers at once).

Threading notes (sync API):
- Mutations are guarded by one threading.RLock. RLock (not Lock) because
  Playwright dispatches the context "close" event synchronously inside
  context.close(), so the on-close purge re-enters the pool from the same
  thread.
- Contexts are created and closed OUTSIDE the lock: both are blocking
  Playwright calls that dispatch events, and holding the lock across them
  would serialize (or deadlock) unrelated pool users.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from playwright.sync_api import Browser, BrowserContext

from .core.context_factory import new_context
from .fingerprint.profiles import FingerprintProfile
from .network.proxy import ProxyProvider


@dataclass
class _Entry:
    context: BrowserContext
    profile: FingerprintProfile
    in_use: bool
    last_used: float
    closed: bool


class SessionPool:
    """
    - browser: a browser from stealth.launch() (or any Chromium browser).
    - max_contexts: capacity; acquiring beyond it recycles the context that
      has been idle the longest.
    - idle_ttl: seconds after which close_idle() reclaims a released context.
    - tls_proxy_pool: optional network.tls_proxy.TlsProxyPool; enables
      acquire(upstream_resolver=...) fingerprint↔egress binding.
    """

    def __init__(self, browser: Browser, *, max_contexts: int = 8,
                 idle_ttl: float = 300, tls_proxy_pool: Optional[Any] = None):
        if max_contexts < 1:
            raise ValueError("max_contexts must be >= 1")
        self._browser = browser
        self._max_contexts = max_contexts
        self._idle_ttl = idle_ttl
        self._tls_proxy_pool = tls_proxy_pool  # network.tls_proxy.TlsProxyPool
        self._entries: Dict[str, _Entry] = {}
        self._lock = threading.RLock()

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _close_entry(entry: Optional[_Entry]) -> None:
        if entry is not None and not entry.closed:
            entry.closed = True
            try:
                entry.context.close()
            except Exception:
                pass

    def _evict_lru_idle(self) -> None:
        """Pops the longest-idle released entry (caller holds the lock)."""
        idle = [e for e in self._entries.values() if not e.in_use]
        if not idle:
            raise RuntimeError(
                f"SessionPool: all {self._max_contexts} contexts are in use; "
                "release one before acquiring another identity"
            )
        oldest = min(idle, key=lambda e: e.last_used)
        self._entries.pop(oldest.profile.id, None)
        self._close_entry(oldest)  # safe: RLock + close-event purge is reentrant

    # -- public API --------------------------------------------------------

    def acquire(
        self,
        profile: FingerprintProfile,
        *,
        proxy_provider: Optional[ProxyProvider] = None,
        storage_state: Optional[Any] = None,
        upstream_resolver: Optional[Any] = None,
    ) -> BrowserContext:
        """
        Returns the pooled context for this profile identity, creating it
        (with the full stealth treatment) on first use. storage_state is
        applied only when the context is first created.

        upstream_resolver: Callable[[str], Optional[str]] mapping
        profile.proxy_id to an egress proxy URL. Requires the pool to have
        been constructed with tls_proxy_pool=TlsProxyPool(); the matching
        per-upstream sidecar (JA3 rewrite + egress chain) is then used for
        this context. Note the binding is fixed at context creation — a
        re-acquired context keeps its original upstream.
        """
        with self._lock:
            entry = self._entries.get(profile.id)
            if entry is not None and entry.closed:
                self._entries.pop(profile.id, None)
                entry = None
            if entry is not None:
                entry.in_use = True
                entry.last_used = time.monotonic()
                return entry.context
            if len(self._entries) >= self._max_contexts:
                self._evict_lru_idle()

        tls_proxy = None
        if upstream_resolver is not None:
            if self._tls_proxy_pool is None:
                raise ValueError(
                    "upstream_resolver requires SessionPool(tls_proxy_pool=...)"
                )
            upstream = upstream_resolver(profile.proxy_id)
            tls_proxy = self._tls_proxy_pool.get(upstream)

        # Create outside the lock — new_context is slow and dispatches events.
        overrides = {"storage_state": storage_state} if storage_state else None
        context = new_context(
            self._browser,
            profile,
            proxy_provider=proxy_provider,
            overrides=overrides,
            tls_proxy=tls_proxy,
        )
        entry = _Entry(
            context=context,
            profile=profile,
            in_use=True,
            last_used=time.monotonic(),
            closed=False,
        )

        def on_close(_: Any) -> None:
            # Crashed or externally closed — purge so the next acquire
            # rebuilds instead of handing out a corpse. Fires synchronously
            # from context.close(); RLock makes that reentrancy safe.
            with self._lock:
                entry.closed = True
                if self._entries.get(profile.id) is entry:
                    self._entries.pop(profile.id, None)

        context.on("close", on_close)
        with self._lock:
            self._entries[profile.id] = entry
        return context

    def release(self, context: BrowserContext) -> None:
        """Returns a context to the idle pool (it stays open and warm)."""
        with self._lock:
            for entry in self._entries.values():
                if entry.context is context:
                    entry.in_use = False
                    entry.last_used = time.monotonic()
                    return

    def close_idle(self) -> int:
        """Closes released contexts idle longer than idle_ttl. Returns count."""
        now = time.monotonic()
        with self._lock:
            stale = [
                e
                for e in self._entries.values()
                if not e.in_use and now - e.last_used > self._idle_ttl
            ]
            for e in stale:
                self._entries.pop(e.profile.id, None)
        for e in stale:
            self._close_entry(e)
        return len(stale)

    def close_all(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for e in entries:
            self._close_entry(e)

    def profile_ids(self) -> List[str]:
        with self._lock:
            return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __enter__(self) -> "SessionPool":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close_all()
