# -*- coding: utf-8 -*-
"""Item 数据模型与去重键。"""

import hashlib

from acquisition.models import Item, dedup_key


def _item(**kwargs):
    base = {"source_id": "s1", "lane": "news"}
    base.update(kwargs)
    return Item(**base)


def test_dedup_key_prefers_platform_and_item_id():
    item = _item(platform="weibo", item_id="123", content="正文")
    assert dedup_key(item) == "weibo:123"


def test_dedup_key_falls_back_to_normalized_content_sha1():
    content = "x" * 500
    item = _item(platform="web", item_id="", content=content)
    normalized = " ".join(content.split())[:2000]
    expected = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    assert dedup_key(item) == f"sha1:{expected}"


def test_dedup_key_same_prefix_different_tail_not_collapsed():
    """P1 回归：财联社式条目共享前 200+ 字符前缀，不得误判为重复。"""
    prefix = "2026-08-03 财联社电报 " + "头" * 200
    first = _item(content=prefix + "甲公司发布公告")
    second = _item(content=prefix + "乙公司发布公告")
    assert dedup_key(first) != dedup_key(second)


def test_dedup_key_whitespace_normalized():
    """换行/多空格抖动不得导致同一条目每轮都被当成新条目。"""
    first = _item(content="同一  条\n内容")
    second = _item(content="同一 条 内容")
    assert dedup_key(first) == dedup_key(second)


def test_dedup_key_same_content_same_key():
    assert dedup_key(_item(content="相同内容")) == dedup_key(_item(content="相同内容"))


def test_item_roundtrip_through_dict():
    item = _item(platform="rss", item_id="id1", title="标题", content="内容", url="http://x")
    restored = Item.from_dict(item.to_dict())
    assert restored == item
    assert restored.fetched_at  # 自动填充 ISO 时间


def test_from_dict_ignores_unknown_fields():
    item = Item.from_dict({"source_id": "s", "lane": "news", "bogus": 1})
    assert item.source_id == "s"
