# -*- coding: utf-8 -*-
"""Tests for the stealth web backend (agent_reach.backends.stealth).

Unit tests mock the stealth_kit rendering layer, so they run everywhere —
including machines without playwright. The integration test at the bottom
renders a JS-built page from a local HTTP server through a real stealth
browser and is skipped unless playwright + a browser channel are available.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import pytest

from agent_reach.backends.stealth import (
    StealthBackend,
    StealthReadError,
    StealthUnavailableError,
    _cookies_for_domain,
    _parse_cookie_value,
)
from agent_reach.channels.web import WebChannel


class _Cfg:
    """Minimal Config stand-in (the backend only calls .get and .data)."""

    def __init__(self, data=None):
        self.data = data or {}

    def get(self, key, default=None):
        return self.data.get(key, default)


# --- cookie parsing / domain filtering ------------------------------------


def test_parse_cookie_value_json_array():
    cookies = _parse_cookie_value(
        json.dumps([{"name": "a", "value": "1", "domain": ".example.com"}])
    )
    assert cookies == [{"name": "a", "value": "1", "domain": ".example.com"}]


def test_parse_cookie_value_header_string():
    cookies = _parse_cookie_value("a=1; b=hello world")
    assert cookies == [{"name": "a", "value": "1"}, {"name": "b", "value": "hello world"}]


def test_cookies_for_domain_filters_by_host():
    cfg = _Cfg(
        {
            "site-cookies": json.dumps(
                [
                    {"name": "keep", "value": "1", "domain": ".example.com"},
                    {"name": "drop", "value": "2", "domain": ".other.org"},
                ]
            ),
            "unrelated_key": "a=1",
        }
    )
    cookies = _cookies_for_domain(cfg, "example.com")
    assert [c["name"] for c in cookies] == ["keep"]


# --- availability paths (no playwright required to test) -------------------


def test_check_reports_off_without_playwright():
    with patch("agent_reach.backends.stealth._playwright_available", return_value=False):
        status, message = StealthBackend(_Cfg()).check()
    assert status == "off"
    assert "agent-reach[browser]" in message


def test_read_raises_unavailable_without_playwright():
    backend = StealthBackend(_Cfg())
    with (
        patch("agent_reach.backends.stealth._playwright_available", return_value=False),
        patch("agent_reach.backends.stealth._runtime") as runtime,
    ):
        runtime.get.side_effect = StealthUnavailableError("playwright 未安装")
        with pytest.raises(StealthUnavailableError):
            backend.read("https://example.com")


# --- read() with a mocked rendering layer ----------------------------------


def _mock_runtime_page(text="rendered text", title="ok"):
    page = MagicMock()
    page.title.return_value = title
    page.query_selector.return_value = True
    page.inner_text.return_value = text
    context = MagicMock()
    context.new_page.return_value = page
    runtime = MagicMock()
    runtime.get.return_value = MagicMock()
    return runtime, context, page


def test_read_returns_rendered_text():
    runtime, context, page = _mock_runtime_page()
    stealth = MagicMock()
    stealth.new_context.return_value = context
    with (
        patch("agent_reach.backends.stealth._runtime", runtime),
        patch.dict("sys.modules", {"stealth_kit": MagicMock(stealth=stealth, BUILTIN_PROFILES=[object()])}),
    ):
        out = StealthBackend(_Cfg()).read("https://example.com")
    assert out == "rendered text"
    context.close.assert_called_once()


def test_read_normalizes_schemeless_url():
    runtime, context, page = _mock_runtime_page()
    stealth = MagicMock()
    stealth.new_context.return_value = context
    with (
        patch("agent_reach.backends.stealth._runtime", runtime),
        patch.dict("sys.modules", {"stealth_kit": MagicMock(stealth=stealth, BUILTIN_PROFILES=[object()])}),
    ):
        StealthBackend(_Cfg()).read("example.com")
    assert page.goto.call_args.args[0] == "https://example.com"


@pytest.mark.parametrize(
    "title,text",
    [
        ("Attention Required! | Cloudflare", ""),
        ("", "Please verify you are human to continue"),
        ("安全验证", ""),
        ("", "请完成安全验证后继续访问"),
    ],
)
def test_read_detects_challenge_pages(title, text):
    runtime, context, page = _mock_runtime_page(text=text, title=title)
    stealth = MagicMock()
    stealth.new_context.return_value = context
    with (
        patch("agent_reach.backends.stealth._runtime", runtime),
        patch.dict("sys.modules", {"stealth_kit": MagicMock(stealth=stealth, BUILTIN_PROFILES=[object()])}),
    ):
        with pytest.raises(StealthReadError, match="反爬验证特征"):
            StealthBackend(_Cfg()).read("https://example.com")


def test_read_wraps_navigation_errors():
    runtime, context, page = _mock_runtime_page()
    page.goto.side_effect = TimeoutError("goto timeout 30000ms exceeded")
    stealth = MagicMock()
    stealth.new_context.return_value = context
    with (
        patch("agent_reach.backends.stealth._runtime", runtime),
        patch.dict("sys.modules", {"stealth_kit": MagicMock(stealth=stealth, BUILTIN_PROFILES=[object()])}),
    ):
        with pytest.raises(StealthReadError, match="渲染失败"):
            StealthBackend(_Cfg()).read("https://example.com")
    context.close.assert_called_once()


def test_read_injects_matching_cookies():
    runtime, context, page = _mock_runtime_page()
    stealth = MagicMock()
    stealth.new_context.return_value = context
    cfg = _Cfg({"site-cookies": json.dumps([{"name": "a", "value": "1", "domain": ".example.com"}])})
    with (
        patch("agent_reach.backends.stealth._runtime", runtime),
        patch.dict("sys.modules", {"stealth_kit": MagicMock(stealth=stealth, BUILTIN_PROFILES=[object()])}),
    ):
        StealthBackend(cfg).read("https://example.com")
    context.add_cookies.assert_called_once()
    assert context.add_cookies.call_args.args[0][0]["name"] == "a"


# --- web channel routing ----------------------------------------------------


def test_web_channel_prefers_jina_and_does_not_touch_stealth():
    channel = WebChannel()
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = b"jina text"
    with (
        patch("urllib.request.urlopen", return_value=cm),
        patch("agent_reach.backends.stealth.StealthBackend.read") as stealth_read,
    ):
        assert channel.read("https://example.com") == "jina text"
    stealth_read.assert_not_called()


def test_web_channel_falls_back_to_stealth_on_jina_failure():
    channel = WebChannel()
    with (
        patch("urllib.request.urlopen", side_effect=OSError("boom")),
        patch(
            "agent_reach.backends.stealth.StealthBackend.read",
            return_value="stealth text",
        ) as stealth_read,
    ):
        assert channel.read("https://example.com") == "stealth text"
    stealth_read.assert_called_once()


def test_web_channel_raises_when_all_backends_fail():
    channel = WebChannel()
    with (
        patch("urllib.request.urlopen", side_effect=OSError("boom")),
        patch(
            "agent_reach.backends.stealth.StealthBackend.read",
            side_effect=StealthUnavailableError("no playwright"),
        ),
    ):
        with pytest.raises(RuntimeError, match="全部后端失败"):
            channel.read("https://example.com")


def test_web_backend_override_forces_stealth_first():
    channel = WebChannel()
    cfg = _Cfg({"web_backend": "stealth"})
    with (
        patch("urllib.request.urlopen") as mock_open,
        patch(
            "agent_reach.backends.stealth.StealthBackend.read",
            return_value="stealth text",
        ),
    ):
        assert channel.read("https://example.com", config=cfg) == "stealth text"
    mock_open.assert_not_called()  # Jina never tried


def test_web_check_forced_stealth_sets_active_backend():
    channel = WebChannel()
    cfg = _Cfg({"web_backend": "stealth"})
    with patch(
        "agent_reach.backends.stealth.StealthBackend.check",
        return_value=("ok", "ready"),
    ):
        status, _ = channel.check(cfg)
    assert status == "ok"
    assert channel.active_backend == "stealth"


# --- integration: real stealth render of a JS-built page --------------------

_JS_PAGE = """<html><head><title>render test</title></head>
<body><div id="app">loading...</div>
<script>
  setTimeout(() => {
    document.getElementById('app').textContent = 'JS_RENDERED_CONTENT_42';
  }, 300);
</script></body></html>"""


def _playwright_and_browser_ready() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    from pathlib import Path

    for cand in (
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    ):
        if cand.is_file():
            return True
    return False


class _Ja3OffConfig:
    """sidecar 缺失时关掉 JA3 重写（localhost 渲染测试不需要 uTLS 出口）。"""

    data: dict = {}

    def get(self, key, default=None):
        return "0" if key == "stealth_ja3" else default


@pytest.mark.skipif(
    not _playwright_and_browser_ready(),
    reason="needs playwright and a chrome/msedge channel",
)
def test_integration_stealth_renders_js_page():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = _JS_PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        # Force the stealth path (Jina cannot see localhost anyway).
        # 本测试只验证 JS 渲染，不验证 JA3：sidecar 二进制缺失的机器上
        # （无 Go 工具链）用 stealth_ja3=0 走无 sidecar 路径。
        from agent_reach.backends.stealth import _sidecar_exe

        config = None if _sidecar_exe().is_file() else _Ja3OffConfig()
        channel = WebChannel()
        with patch("urllib.request.urlopen", side_effect=OSError("no jina")):
            out = channel.read(url, config=config)
        assert "JS_RENDERED_CONTENT_42" in out
    finally:
        server.shutdown()
        StealthBackend.close()
