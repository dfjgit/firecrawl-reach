# -*- coding: utf-8 -*-
"""web 渠道：Jina → firecrawl → stealth 回退链。"""

from unittest.mock import patch

import pytest

from agent_reach.channels.web import WebChannel
from agent_reach.firecrawl_client import FirecrawlUnavailableError


def _read(config=None, url="https://example.com"):
    return WebChannel().read(url, config=config)


class TestFallbackChain:
    def test_jina_ok_short_circuits(self):
        with patch.object(WebChannel, "_read_jina", return_value="jina"):
            assert _read() == "jina"

    def test_jina_fails_firecrawl_serves(self):
        with patch.object(WebChannel, "_read_jina", side_effect=RuntimeError("x")), \
             patch("agent_reach.backends.firecrawl.FirecrawlBackend.read",
                   return_value="fc") as fc_read:
            assert _read() == "fc"
        fc_read.assert_called_once()

    def test_firecrawl_fails_stealth_serves(self):
        with patch.object(WebChannel, "_read_jina", side_effect=RuntimeError("x")), \
             patch("agent_reach.backends.firecrawl.FirecrawlBackend.read",
                   side_effect=FirecrawlUnavailableError("down")), \
             patch("agent_reach.backends.stealth.StealthBackend.read",
                   return_value="stealth"):
            assert _read() == "stealth"

    def test_all_fail_raises_with_backend_names(self):
        with patch.object(WebChannel, "_read_jina", side_effect=RuntimeError("j")), \
             patch("agent_reach.backends.firecrawl.FirecrawlBackend.read",
                   side_effect=FirecrawlUnavailableError("f")), \
             patch("agent_reach.backends.stealth.StealthBackend.read",
                   side_effect=RuntimeError("s")):
            with pytest.raises(RuntimeError) as exc_info:
                _read()
        msg = str(exc_info.value)
        assert "firecrawl" in msg and "stealth" in msg

    def test_disabled_firecrawl_costs_nothing(self):
        # 未配置 firecrawl_enabled 时后端立即抛 Unavailable，不打任何 HTTP
        with patch.object(WebChannel, "_read_jina", side_effect=RuntimeError("x")), \
             patch("agent_reach.firecrawl_client.FirecrawlClient.scrape") as scrape, \
             patch("agent_reach.backends.stealth.StealthBackend.read",
                   return_value="stealth"):
            assert _read(config={}) == "stealth"
        scrape.assert_not_called()
