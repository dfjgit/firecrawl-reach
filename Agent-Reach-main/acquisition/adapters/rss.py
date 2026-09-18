# -*- coding: utf-8 -*-
"""RSS/Atom 适配器（L0 结构化抽取，有 RSS 永远首选）。"""

import feedparser

from acquisition.adapters.base import Adapter, AdapterError
from acquisition.models import Item
from acquisition.sources import Source


class RssAdapter(Adapter):
    """feedparser 解析，条目映射 title/link/published/summary→content。

    部分源要求自定义 UA（如 SEC EDGAR 需声明身份的 UA，否则 403）：
    源配 ``extra_args.user_agent`` 时传给 feedparser；未配用默认。
    """

    def fetch(self, source: Source) -> list:
        kwargs = {}
        ua = (source.extra_args or {}).get("user_agent")
        if ua:
            kwargs["request_headers"] = {"User-Agent": str(ua)}
        feed = feedparser.parse(source.url, **kwargs)
        if feed.bozo and not feed.entries:
            raise AdapterError(f"RSS 解析失败 {source.url}: {feed.bozo_exception}")
        items = []
        for entry in feed.entries[: source.max_items]:
            items.append(
                Item(
                    source_id=source.id,
                    lane=source.lane,
                    platform=source.platform or "rss",
                    item_id=str(entry.get("id") or entry.get("link") or ""),
                    author=source.name,
                    title=entry.get("title") or None,
                    content=str(entry.get("summary") or entry.get("title") or ""),
                    url=str(entry.get("link") or ""),
                    published_at=str(
                        entry.get("published") or entry.get("updated") or ""
                    ),
                    snapshot=False,
                )
            )
        return items


def fetch(source: Source) -> list:
    """模块级便捷入口（路由器按 kind 分发用）。"""
    return RssAdapter().fetch(source)
