# -*- coding: utf-8 -*-
"""L1 规则切分与 L2 整页快照。"""

from acquisition.extractors import make_snapshot, split_by_pattern

TELEGRAPH = """财联社电报

12:30
【第一条】内容甲
更多细节
12:15
【第二条】内容乙
11:58
【第三条】内容丙
"""


def test_l1_split_by_timestamp_pattern():
    chunks = split_by_pattern(TELEGRAPH, r"^\d{2}:\d{2}", max_items=30)
    assert len(chunks) == 3
    assert chunks[0].startswith("12:30")
    assert "内容甲" in chunks[0] and "更多细节" in chunks[0]
    assert chunks[1].startswith("12:15")
    assert chunks[2].startswith("11:58")


def test_l1_respects_max_items():
    chunks = split_by_pattern(TELEGRAPH, r"^\d{2}:\d{2}", max_items=2)
    assert len(chunks) == 2


def test_l1_no_match_returns_empty():
    assert split_by_pattern("没有时间戳的页面", r"^\d{2}:\d{2}", max_items=30) == []


def test_l2_snapshot_truncates():
    text = "  abc" + "x" * 100 + "  "
    assert make_snapshot(text, max_chars=50) == "abc" + "x" * 47


def test_l2_snapshot_short_text_unchanged():
    assert make_snapshot("  短文本  ", max_chars=4000) == "短文本"
