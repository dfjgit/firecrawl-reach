# -*- coding: utf-8 -*-
"""FirecrawlBackend：web 渠道的 firecrawl 单页后端。"""

from unittest.mock import MagicMock, patch

import pytest

from agent_reach.backends.firecrawl import FirecrawlBackend, FirecrawlReadError
from agent_reach.firecrawl_client import FirecrawlUnavailableError

_ENABLED = {"firecrawl_enabled": "true"}


class TestEnabled:
    def test_read_disabled_raises_unavailable(self):
        with pytest.raises(FirecrawlUnavailableError, match="未启用"):
            FirecrawlBackend({}).read("https://example.com")

    def test_check_disabled_is_off(self):
        status, msg = FirecrawlBackend({}).check()
        assert status == "off"
        assert "未启用" in msg


class TestRead:
    def _client(self, data):
        client = MagicMock()
        client.scrape.return_value = data
        return client

    def test_returns_markdown(self):
        with patch("agent_reach.backends.firecrawl.FirecrawlClient") as cls:
            cls.from_config.return_value = self._client(
                {"markdown": "# 正文", "metadata": {"title": "t"}}
            )
            text = FirecrawlBackend(_ENABLED).read("example.com")
        assert text == "# 正文"
        # 缺 scheme 自动补 https://
        cls.from_config.return_value.scrape.assert_called_with("https://example.com")

    def test_challenge_marker_raises_read_error(self):
        with patch("agent_reach.backends.firecrawl.FirecrawlClient") as cls:
            cls.from_config.return_value = self._client(
                {"markdown": "Please verify you are human", "metadata": {}}
            )
            with pytest.raises(FirecrawlReadError, match="反爬验证特征"):
                FirecrawlBackend(_ENABLED).read("https://example.com")

    def test_empty_markdown_raises_read_error(self):
        with patch("agent_reach.backends.firecrawl.FirecrawlClient") as cls:
            cls.from_config.return_value = self._client({"markdown": "  "})
            with pytest.raises(FirecrawlReadError, match="空内容"):
                FirecrawlBackend(_ENABLED).read("https://example.com")

    def test_client_error_propagates(self):
        with patch("agent_reach.backends.firecrawl.FirecrawlClient") as cls:
            cls.from_config.return_value.scrape.side_effect = (
                FirecrawlUnavailableError("down")
            )
            with pytest.raises(FirecrawlUnavailableError):
                FirecrawlBackend(_ENABLED).read("https://example.com")


class TestCheck:
    def test_probe_ok(self):
        with patch("agent_reach.backends.firecrawl.FirecrawlClient") as cls:
            cls.from_config.return_value.base_url = "http://localhost:3002"
            status, msg = FirecrawlBackend(_ENABLED).check(probe=True)
        assert status == "ok"
        assert "可达" in msg

    def test_probe_error(self):
        with patch("agent_reach.backends.firecrawl.FirecrawlClient") as cls:
            cls.from_config.return_value.ping.side_effect = (
                FirecrawlUnavailableError("refused")
            )
            status, msg = FirecrawlBackend(_ENABLED).check(probe=True)
        assert status == "error"
