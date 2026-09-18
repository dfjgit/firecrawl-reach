# -*- coding: utf-8 -*-
"""firecrawl_crawl 适配器：整站爬取源。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from acquisition.adapters.base import AdapterError
from acquisition.adapters.firecrawl import FirecrawlCrawlAdapter
from acquisition.sources import SourceConfigError, load_sources


def _source(**overrides):
    base = dict(
        id="fc1", name="示例站", lane="news", kind="firecrawl_crawl",
        url="https://docs.example.com", interval=300, max_items=10,
        enabled=True, platform="", crawler_type="", creator_id="",
        command=[], extra_args={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


_PAGES = [
    {"markdown": "# 页面一", "metadata": {"sourceURL": "https://docs.example.com/a",
                                          "title": "页面一"}},
    {"markdown": "# 页面二", "metadata": {"sourceURL": "https://docs.example.com/b"}},
    {"markdown": "", "metadata": {"sourceURL": "https://docs.example.com/empty"}},
]


class TestFetch:
    def test_pages_become_items(self):
        client = MagicMock()
        client.crawl.return_value = _PAGES
        items = FirecrawlCrawlAdapter(client=client).fetch(_source())
        assert len(items) == 2  # 空 markdown 页面被跳过
        assert items[0].title == "页面一"
        assert items[0].url == "https://docs.example.com/a"
        assert items[0].content == "# 页面一"
        assert items[0].snapshot is True
        assert items[1].title == "https://docs.example.com/b"  # 无 title 退化为 URL
        client.crawl.assert_called_once_with("https://docs.example.com", limit=10)

    def test_crawl_limit_from_extra_args(self):
        client = MagicMock()
        client.crawl.return_value = _PAGES
        FirecrawlCrawlAdapter(client=client).fetch(
            _source(extra_args={"crawl_limit": 50})
        )
        client.crawl.assert_called_once_with("https://docs.example.com", limit=50)

    def test_client_error_wrapped(self):
        from agent_reach.firecrawl_client import FirecrawlUnavailableError

        client = MagicMock()
        client.crawl.side_effect = FirecrawlUnavailableError("down")
        with pytest.raises(AdapterError, match="整站爬取失败"):
            FirecrawlCrawlAdapter(client=client).fetch(_source())

    def test_empty_result_raises(self):
        client = MagicMock()
        client.crawl.return_value = []
        with pytest.raises(AdapterError, match="为空"):
            FirecrawlCrawlAdapter(client=client).fetch(_source())


class TestSourceValidation:
    def test_kind_accepted(self, tmp_path):
        sources_file = tmp_path / "sources.yaml"
        sources_file.write_text(
            "- id: fc1\n"
            "  lane: news\n"
            "  kind: firecrawl_crawl\n"
            "  url: https://docs.example.com\n",
            encoding="utf-8",
        )
        sources = load_sources(sources_file)
        assert sources[0].kind == "firecrawl_crawl"

    def test_url_required(self, tmp_path):
        sources_file = tmp_path / "sources.yaml"
        sources_file.write_text(
            "- id: fc1\n"
            "  lane: news\n"
            "  kind: firecrawl_crawl\n",
            encoding="utf-8",
        )
        with pytest.raises(SourceConfigError, match="url"):
            load_sources(sources_file)
