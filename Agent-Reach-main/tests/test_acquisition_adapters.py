# -*- coding: utf-8 -*-
"""适配器：rss 用 fixture XML（不联网），stealth 用 mock 的 StealthBackend。"""

import pytest

from acquisition.adapters import rss, stealth
from acquisition.adapters.base import AdapterError
from acquisition.sources import Source

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>36氪</title>
    <item>
      <title>快讯一</title>
      <link>https://36kr.com/p/1001</link>
      <guid>https://36kr.com/p/1001</guid>
      <pubDate>Mon, 03 Aug 2026 08:00:00 GMT</pubDate>
      <description>第一条摘要</description>
    </item>
    <item>
      <title>快讯二</title>
      <link>https://36kr.com/p/1002</link>
      <pubDate>Mon, 03 Aug 2026 07:00:00 GMT</pubDate>
      <description>第二条摘要</description>
    </item>
    <item>
      <title>快讯三</title>
      <link>https://36kr.com/p/1003</link>
      <description>第三条摘要</description>
    </item>
  </channel>
</rss>
"""


def _rss_source(**overrides):
    base = {"id": "kr36-rss", "name": "36氪", "lane": "news", "kind": "rss",
            "url": "https://36kr.com/feed"}
    base.update(overrides)
    return Source(**base)


def _stealth_source(**overrides):
    base = {"id": "cls-telegraph", "name": "财联社电报", "lane": "news",
            "kind": "stealth", "url": "https://www.cls.cn/telegraph"}
    base.update(overrides)
    return Source(**base)


# ── rss 适配器 ────────────────────────────────────


def _patch_feedparser(monkeypatch, xml):
    """把 feedparser.parse 换成只解析本地 fixture XML 的版本（不联网）。"""
    import feedparser

    original = feedparser.parse
    monkeypatch.setattr(feedparser, "parse", lambda url: original(xml))


def test_rss_adapter_maps_entries(monkeypatch):
    _patch_feedparser(monkeypatch, RSS_XML)
    items = rss.fetch(_rss_source())
    assert len(items) == 3
    first, second, third = items
    assert first.title == "快讯一"
    assert first.url == "https://36kr.com/p/1001"
    assert first.item_id == "https://36kr.com/p/1001"  # guid 优先
    assert first.content == "第一条摘要"
    assert first.published_at == "Mon, 03 Aug 2026 08:00:00 GMT"
    assert first.source_id == "kr36-rss"
    assert first.lane == "news"
    assert first.snapshot is False
    # 无 guid 时退回 link 作为 item_id
    assert second.item_id == "https://36kr.com/p/1002"
    assert third.item_id == "https://36kr.com/p/1003"


def test_rss_adapter_respects_max_items(monkeypatch):
    _patch_feedparser(monkeypatch, RSS_XML)
    items = rss.fetch(_rss_source(max_items=2))
    assert len(items) == 2


def test_rss_adapter_raises_on_broken_feed(monkeypatch):
    _patch_feedparser(monkeypatch, "不是 XML")
    with pytest.raises(AdapterError, match="RSS 解析失败"):
        rss.fetch(_rss_source())


# ── stealth 适配器 ────────────────────────────────


class _FakeBackend:
    def __init__(self, text=None, exc=None):
        self._text = text
        self._exc = exc

    def read(self, url):
        if self._exc is not None:
            raise self._exc
        return self._text


def test_stealth_adapter_l1_with_item_pattern():
    text = "页面头部\n12:30\n【电报一】甲\n12:15\n【电报二】乙\n"
    source = _stealth_source(extra_args={"item_pattern": r"^\d{2}:\d{2}"})
    items = stealth.StealthAdapter(backend=_FakeBackend(text=text)).fetch(source)
    assert len(items) == 2
    assert items[0].content.startswith("12:30")
    assert items[0].title == "12:30"  # 首行作标题，下游按标题去重/展示用
    assert items[1].title == "12:15"
    assert items[0].snapshot is False
    assert items[0].url == source.url
    assert items[0].author == "财联社电报"


def test_stealth_adapter_default_backend_uses_real_config(monkeypatch):
    """P0 回归：默认构造必须注入真实 Config。

    裸 StealthBackend() 的 _NullConfig 让 stealth_ja3 恒为默认开启，
    无 Go sidecar 的机器上所有 stealth 源会直接报 StealthUnavailableError。
    """
    created = {}

    class _SpyBackend:
        def __init__(self, config=None):
            created["config"] = config

        def read(self, url):
            return "整页文本"

    fake_config = object()
    monkeypatch.setattr(stealth, "StealthBackend", _SpyBackend)
    monkeypatch.setattr(stealth, "Config", lambda read_only=True: fake_config)

    items = stealth.StealthAdapter().fetch(_stealth_source())

    assert created["config"] is fake_config
    assert len(items) == 1


def test_stealth_adapter_falls_back_to_l2_snapshot():
    source = _stealth_source(extra_args={"item_pattern": r"^\d{2}:\d{2}"})
    items = stealth.StealthAdapter(backend=_FakeBackend(text="切不动的整页文本")).fetch(source)
    assert len(items) == 1
    assert items[0].snapshot is True
    assert items[0].content == "切不动的整页文本"
    assert items[0].item_id == ""  # L2 无 item_id，去重键退化内容哈希


def test_stealth_adapter_l2_when_no_pattern():
    items = stealth.StealthAdapter(backend=_FakeBackend(text="整页")).fetch(_stealth_source())
    assert len(items) == 1 and items[0].snapshot is True


def test_stealth_adapter_wraps_unavailable_error():
    from agent_reach.backends.stealth import StealthUnavailableError

    backend = _FakeBackend(exc=StealthUnavailableError("playwright 未安装"))
    with pytest.raises(AdapterError, match="playwright 未安装"):
        stealth.StealthAdapter(backend=backend).fetch(_stealth_source())


def test_stealth_adapter_wraps_read_error():
    from agent_reach.backends.stealth import StealthReadError

    backend = _FakeBackend(exc=StealthReadError("命中反爬验证"))
    with pytest.raises(AdapterError, match="命中反爬验证"):
        stealth.StealthAdapter(backend=backend).fetch(_stealth_source())


def test_stealth_adapter_empty_text_is_error():
    with pytest.raises(AdapterError, match="渲染结果为空"):
        stealth.StealthAdapter(backend=_FakeBackend(text="  ")).fetch(_stealth_source())
