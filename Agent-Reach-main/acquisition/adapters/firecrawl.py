# -*- coding: utf-8 -*-
"""firecrawl_crawl 适配器：整站爬取源（firecrawl /crawl）。

入口 URL 整站爬取，每页一条 snapshot Item；空 markdown 页跳过。
增量去重交给 router/state.db（按 URL/标题判重，与 rss/stealth 源一致）。

backend 必须带真实 Config（firecrawl_enabled/firecrawl_url 由它控制）；
未启用时直接 AdapterError，由 router 记 error 下轮重试。
"""

from acquisition.adapters.base import Adapter, AdapterError
from acquisition.models import Item
from acquisition.sources import Source
from agent_reach.config import Config
from agent_reach.firecrawl_client import (
    FirecrawlClient,
    FirecrawlError,
    firecrawl_enabled,
)


class FirecrawlCrawlAdapter(Adapter):
    """整站爬取：每页 markdown 作为一条 snapshot Item。"""

    def __init__(self, client=None) -> None:
        # client 可注入（测试 mock）；None 时每次 fetch 由 Config 构造
        self._client = client

    def fetch(self, source: Source) -> list:
        if self._client is not None:
            client = self._client
        else:
            config = Config(read_only=True)
            if not firecrawl_enabled(config):
                raise AdapterError(
                    "firecrawl 未启用（agent-reach configure firecrawl-enabled true）"
                )
            client = FirecrawlClient.from_config(config)

        limit = int(source.extra_args.get("crawl_limit", source.max_items))
        try:
            pages = client.crawl(source.url, limit=limit)
        except FirecrawlError as exc:
            raise AdapterError(f"firecrawl 整站爬取失败 {source.url}：{exc}") from exc

        items = []
        for page in pages[: source.max_items]:
            markdown = (page.get("markdown") or "").strip()
            if not markdown:
                continue
            metadata = page.get("metadata") or {}
            page_url = str(
                metadata.get("sourceURL") or metadata.get("url") or source.url
            )
            items.append(
                Item(
                    source_id=source.id,
                    lane=source.lane,
                    platform=source.platform or "web",
                    author=source.name,
                    title=str(metadata.get("title") or page_url)[:80],
                    content=markdown,
                    url=page_url,
                    published_at="",
                    snapshot=True,
                )
            )
        if not items:
            raise AdapterError(f"firecrawl 整站爬取结果为空：{source.url}")
        return items


def fetch(source: Source) -> list:
    """模块级便捷入口（路由器按 kind 分发用）。"""
    return FirecrawlCrawlAdapter().fetch(source)
