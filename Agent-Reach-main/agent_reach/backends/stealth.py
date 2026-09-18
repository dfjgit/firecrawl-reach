# -*- coding: utf-8 -*-
"""Stealth backend — renders pages in a stealth browser via ``stealth_kit``.

Used by the ``web`` channel as the fallback after Jina Reader: when a page
needs real rendering (or actively blocks plain HTTP), this backend drives a
fingerprint-consistent Chromium through the bundled ``stealth_kit`` package
(init-script patches + CDP overrides + optional uTLS sidecar for a Chrome
JA3/JA4).

Design rules (mirroring the project's backend conventions):
  - playwright stays OPTIONAL: nothing here imports it at module level; the
    first read() (or check with launch probe) is the first touch. Without
    playwright the backend simply reports itself unavailable and users of
    other backends are unaffected.
  - The browser process is a lazily-started module-level singleton, reused
    across reads and torn down at exit (atexit) or via close().
  - Failures raise StealthReadError so the channel can fall through to the
    next backend; missing runtime pieces raise StealthUnavailableError.

Config keys (all optional):
  proxy           — upstream egress proxy (http:// or socks5://, optional
                    user:pass@). When set, the uTLS sidecar starts with this
                    upstream: browser -> sidecar (JA3 rewrite) -> egress.
  stealth_timeout — per-page navigation timeout in seconds (default 30).
  stealth_ja3     — "0"/"false" disables the sidecar (default: on).
  stealth_channel — browser channel override (default: auto chrome->msedge).
  <site>-cookies  — Cookie-Editor exports (JSON array or header string);
                    cookies matching the target domain are injected into the
                    render context. TODO: persist cookies captured during a
                    render back into config (next iteration, one-way now).
"""

import atexit
import json
import os
import threading
from typing import TYPE_CHECKING, List, Optional, Tuple, cast

if TYPE_CHECKING:
    from playwright._impl._api_structures import SetCookieParam
    from playwright.sync_api import Browser

# Common anti-bot challenge markers (title/body, case-insensitive).
_CHALLENGE_MARKERS = (
    "captcha",
    "cf-challenge",
    "challenge-platform",
    "verify you are human",
    "checking your browser",
    "attention required",
    "请完成安全验证",
    "安全验证",
    "滑动验证",
)

_DEFAULT_TIMEOUT = 30
INSTALL_HINT = "pip install agent-reach[browser]"


class StealthUnavailableError(RuntimeError):
    """The stealth runtime (playwright / browser / sidecar) is not usable."""


class StealthReadError(RuntimeError):
    """A single read failed (timeout, challenge page, navigation error)."""


def _playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401

        return True
    except ImportError:
        return False


def _sidecar_exe_name() -> str:
    return "tlsproxy.exe" if os.name == "nt" else "tlsproxy"


def _sidecar_exe():
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "sidecar" / "bin" / _sidecar_exe_name()


class _StealthRuntime:
    """Lazily-started, process-wide browser + sidecar, reused across reads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._browser: Optional["Browser"] = None
        self._closed = False

    def get(self, config) -> "Browser":
        """Returns the shared browser, starting it (and the sidecar) lazily."""
        with self._lock:
            if self._browser is not None:
                return self._browser
            if self._closed:
                raise StealthUnavailableError("stealth runtime already shut down")
            if not _playwright_available():
                raise StealthUnavailableError(
                    f"playwright 未安装，stealth 后端不可用。安装：{INSTALL_HINT}"
                )
            from stealth_kit import stealth

            headless = True
            ja3 = str(config.get("stealth_ja3", "1")).lower() not in ("0", "false", "no")
            upstream = config.get("proxy") if ja3 else None
            channel = config.get("stealth_channel") or "chrome"
            try:
                self._browser = stealth.launch(
                    channel=channel, headless=headless, ja3=ja3, upstream=upstream
                )
            except Exception:
                # chrome is the default; fall back to msedge before giving up.
                if channel != "chrome":
                    raise
                try:
                    self._browser = stealth.launch(
                        channel="msedge", headless=headless, ja3=ja3, upstream=upstream
                    )
                except Exception as exc:
                    raise StealthUnavailableError(
                        f"无法启动浏览器（chrome/msedge）：{exc}"
                    ) from exc
            return self._browser

    def close(self) -> None:
        with self._lock:
            self._closed = True
            browser, self._browser = self._browser, None
        if browser is not None:
            try:
                browser.close()  # stealth.launch ties sidecar/driver to this
            except Exception:
                pass


_runtime = _StealthRuntime()
atexit.register(_runtime.close)


def _parse_cookie_value(value: str) -> List[dict]:
    """Parses a stored cookie value: Cookie-Editor JSON array or header string."""
    value = value.strip()
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except (ValueError, UnicodeDecodeError):
            return []
        return [c for c in parsed if isinstance(c, dict) and c.get("name")]
    # Header String: "name1=value1; name2=value2"
    cookies = []
    for part in value.split(";"):
        if "=" in part:
            name, _, val = part.partition("=")
            name = name.strip()
            if name:
                cookies.append({"name": name, "value": val.strip()})
    return cookies


def _cookies_for_domain(config, host: str) -> List[dict]:
    """Collects config-stored cookies matching the target domain."""
    from agent_reach.utils.url import domain_matches

    matched = []
    for key, value in (config.data or {}).items():
        if not key.endswith("-cookies") or not isinstance(value, str):
            continue
        for cookie in _parse_cookie_value(value):
            domain = cookie.get("domain") or host
            if domain_matches(domain.lstrip("."), host) or domain_matches(host, domain.lstrip(".")):
                matched.append(cookie)
    return matched


class StealthBackend:
    """Renders URLs in a stealth browser. See module docstring for config."""

    name = "stealth"

    def __init__(self, config=None) -> None:
        # config may be None (tests / direct use) — behave like an empty one.
        self.config = config if config is not None else _NullConfig()

    # -- availability / doctor probe --------------------------------------

    def check(self, launch_probe: bool = True) -> Tuple[str, str]:
        """Doctor-style probe: (status, message) with status ok/off/error."""
        if not _playwright_available():
            return "off", f"stealth 后端需要 playwright：{INSTALL_HINT}"
        if not _sidecar_exe().is_file():
            name = _sidecar_exe_name()
            return "error", (
                f"sidecar 二进制缺失（sidecar/bin/{name}）。"
                f"构建：cd sidecar && go build -o bin/{name} ./cmd/tlsproxy"
            )
        if launch_probe:
            try:
                _runtime.get(self.config)
            except StealthUnavailableError as exc:
                return "off", str(exc)
            except Exception as exc:  # pragma: no cover - defensive
                return "error", f"stealth 浏览器启动失败：{exc}"
        return "ok", "stealth 浏览器渲染（stealth_kit + uTLS sidecar，可用作 web 兜底）"

    # -- rendering ---------------------------------------------------------

    def read(self, url: str) -> str:
        """Renders ``url`` and returns the page's readable text.

        Raises StealthUnavailableError when the runtime cannot start, and
        StealthReadError for per-page failures (timeout, challenge page) —
        the channel turns both into a fall-through to the next backend.
        """
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        browser = _runtime.get(self.config)  # raises StealthUnavailableError
        timeout = int(self.config.get("stealth_timeout", _DEFAULT_TIMEOUT)) * 1000

        from stealth_kit import BUILTIN_PROFILES, stealth

        context = None
        try:
            context = stealth.new_context(browser, profile=BUILTIN_PROFILES[0])
            cookies = _cookies_for_domain(config=self.config, host=_host_of(url))
            if cookies:
                context.add_cookies(cast(List["SetCookieParam"], cookies))
            page = context.new_page()
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            # Let client-side rendering settle (JS-inserted content).
            page.wait_for_timeout(1500)
            title = page.title() or ""
            text = page.inner_text("body") if page.query_selector("body") else ""
            self._raise_if_challenge(title, text, url)
            return text or page.content()
        except (StealthReadError, StealthUnavailableError):
            raise
        except Exception as exc:
            raise StealthReadError(f"stealth 渲染失败 {url}: {exc}") from exc
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass

    @staticmethod
    def _raise_if_challenge(title: str, text: str, url: str) -> None:
        haystack = f"{title}\n{text[:2000]}".lower()
        for marker in _CHALLENGE_MARKERS:
            if marker in haystack:
                raise StealthReadError(
                    f"{url} 命中反爬验证特征（{marker}），渲染结果不可用"
                )

    @staticmethod
    def close() -> None:
        """Shuts the shared browser down (atexit already hooks this)."""
        _runtime.close()


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).hostname or ""


class _NullConfig:
    """Config stand-in when no Config is provided (matches Config.get API)."""

    data: dict = {}

    def get(self, key, default=None):
        return default
