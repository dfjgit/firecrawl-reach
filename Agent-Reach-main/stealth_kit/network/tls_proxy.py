"""
TlsProxy: manages the Go uTLS sidecar (sidecar/bin/tlsproxy — tlsproxy.exe
on Windows).

The sidecar is a local MITM forward proxy: the browser terminates TLS with
a minted site certificate; the sidecar re-originates the upstream handshake
with a genuine Chrome ClientHello (uTLS), so the peer sees a Chrome JA3/JA4
instead of the automation browser's real fingerprint.

Usage:

    from stealth_kit.network.tls_proxy import TlsProxy

    with TlsProxy() as proxy:
        context = stealth.new_context(browser, profile=p, tls_proxy=proxy)

The CA is generated once into sidecar/ca/ and reused across runs. Install
sidecar/ca/cert.pem into the OS trust store if you want to drop
ignore_https_errors; the context factory sets it automatically otherwise.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

from .proxy import ProxyConfig

if TYPE_CHECKING:
    from ..fingerprint.profiles import FingerprintProfile

# Repo root: network/tls_proxy.py -> network -> stealth_kit -> root
_ROOT = Path(__file__).resolve().parents[2]
_SIDECAR_DIR = _ROOT / "sidecar"
_EXE_NAME = "tlsproxy.exe" if os.name == "nt" else "tlsproxy"
_EXE = _SIDECAR_DIR / "bin" / _EXE_NAME

_live: "List[TlsProxy]" = []
_live_lock = threading.Lock()


def _atexit_stop() -> None:
    with _live_lock:
        proxies = list(_live)
    for p in proxies:
        p.stop()


atexit.register(_atexit_stop)


def _build_sidecar() -> None:
    go = shutil.which("go")
    if go is None:
        raise FileNotFoundError(
            f"sidecar binary not found at {_EXE} and 'go' is not on PATH to "
            f"build it (go build -o bin/{_EXE_NAME} ./cmd/tlsproxy)"
        )
    subprocess.run(
        [go, "build", "-o", f"bin/{_EXE_NAME}", "./cmd/tlsproxy"],
        cwd=_SIDECAR_DIR,
        check=True,
    )


class TlsProxy:
    """Owns one sidecar process. Thread-safe enough for the sync API.

    - upstream: optional egress proxy for proxy chaining
      (``http://user:pass@host:port`` or ``socks5://user:pass@host:port``).
      With it, traffic flows browser -> sidecar (JA3 rewrite) -> egress ->
      target, so the target sees the egress IP with a Chrome ClientHello.
    """

    def __init__(
        self,
        ca_dir: Optional[Path] = None,
        idle_timeout: str = "120s",
        upstream: Optional[str] = None,
    ):
        if not _EXE.is_file():
            _build_sidecar()
        self._ca_dir = Path(ca_dir) if ca_dir else _SIDECAR_DIR / "ca"
        self._ca_dir.mkdir(parents=True, exist_ok=True)
        self._idle_timeout = idle_timeout
        self._upstream = upstream
        self._proc: Optional[subprocess.Popen] = None
        self._addr: Optional[str] = None
        self._start()

    def _start(self) -> None:
        argv = [
            str(_EXE),
            "-addr", "127.0.0.1:0",
            "-cadir", str(self._ca_dir),
            "-idle", self._idle_timeout,
        ]
        if self._upstream:
            argv += ["-upstream", self._upstream]
        # stderr carries logs; stdout's first line is the listen address.
        self._proc = subprocess.Popen(
            argv,
            cwd=_SIDECAR_DIR,
            stdout=subprocess.PIPE,
            stderr=None,  # inherit: sidecar logs show up in our stderr
            text=True,
        )
        assert self._proc.stdout is not None
        line = self._proc.stdout.readline().strip()
        if not line:
            code = self._proc.wait()
            raise RuntimeError(f"tlsproxy exited immediately (code {code})")
        self._addr = line
        with _live_lock:
            _live.append(self)

    @property
    def address(self) -> str:
        if self._addr is None:
            raise RuntimeError("TlsProxy is stopped")
        return self._addr

    @property
    def upstream(self) -> Optional[str]:
        """The egress proxy URL this sidecar chains through, if any."""
        return self._upstream

    @property
    def proxy(self) -> ProxyConfig:
        """Playwright proxy config pointing at this sidecar."""
        return {"server": f"http://{self.address}"}

    @property
    def ca_cert_path(self) -> Path:
        return self._ca_dir / "cert.pem"

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        with _live_lock:
            if self in _live:
                _live.remove(self)
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._addr = None

    def __enter__(self) -> "TlsProxy":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def __del__(self) -> None:  # best-effort; atexit is the real safety net
        try:
            self.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# TlsProxyPool: one sidecar process per distinct egress upstream.
#
# The sidecar's -upstream is a process-level flag, so profiles that need
# different egress IPs need different sidecar processes. This pool caches
# one TlsProxy per upstream string (None = direct egress) and hands them out
# by profile via a resolver.
# ---------------------------------------------------------------------------

# Maps a profile's proxy_id to an egress upstream URL (or None for direct).
UpstreamResolver = Callable[[str], Optional[str]]


class TlsProxyPool:
    """
    Lazy cache of TlsProxy processes keyed by upstream URL.

    Usage:

        pool = TlsProxyPool()
        resolver = lambda proxy_id: "http://user:pass@gw.example:8080" \\
            if proxy_id.startswith("us-") else None
        tp = pool.for_profile(profile, resolver)
        ctx = stealth.new_context(browser, profile=profile, tls_proxy=tp)
    """

    def __init__(self, idle_timeout: str = "120s"):
        self._idle_timeout = idle_timeout
        self._by_upstream: Dict[Optional[str], TlsProxy] = {}
        self._lock = threading.Lock()

    def get(self, upstream: Optional[str] = None) -> TlsProxy:
        """Returns the cached sidecar for this upstream, starting it lazily."""
        with self._lock:
            tp = self._by_upstream.get(upstream)
            if tp is not None and tp.running:
                return tp
            tp = TlsProxy(idle_timeout=self._idle_timeout, upstream=upstream)
            self._by_upstream[upstream] = tp
            return tp

    def for_profile(
        self, profile: "FingerprintProfile", resolver: UpstreamResolver
    ) -> TlsProxy:
        """Resolves profile.proxy_id to an upstream and returns its sidecar."""
        return self.get(resolver(profile.proxy_id))

    def stop_all(self) -> None:
        with self._lock:
            proxies = list(self._by_upstream.values())
            self._by_upstream.clear()
        for tp in proxies:
            tp.stop()

    def __len__(self) -> int:
        return len(self._by_upstream)

    def __enter__(self) -> "TlsProxyPool":
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop_all()
