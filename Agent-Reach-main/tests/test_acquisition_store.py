# -*- coding: utf-8 -*-
"""存储层：baseline 语义、增量去重、JSONL 读写、节流时间戳。"""

import json

from acquisition.models import Item
from acquisition.store import Store


def _item(item_id, content="内容"):
    return Item(source_id="s1", lane="news", platform="rss", item_id=item_id, content=content)


def test_baseline_first_run_marks_seen_not_new(tmp_path):
    store = Store(tmp_path)
    result = store.record_items("s1", [_item("a"), _item("b")])
    assert result["new"] == []
    assert result["dup"] == 0
    assert result["baseline"] == 2
    assert store.baseline_done("s1") is True
    # baseline 条目不写入 JSONL
    assert not (tmp_path / "data" / "s1.jsonl").exists()


def test_second_run_new_and_dup(tmp_path):
    store = Store(tmp_path)
    store.record_items("s1", [_item("a"), _item("b")])
    result = store.record_items("s1", [_item("b"), _item("c")])
    assert [i.item_id for i in result["new"]] == ["c"]
    assert result["dup"] == 1
    assert result["baseline"] == 0
    # 只有新条目追加进 JSONL
    lines = (tmp_path / "data" / "s1.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["item_id"] == "c"


def test_last_fetched_at_updates_after_record(tmp_path):
    store = Store(tmp_path)
    assert store.last_fetched_at("s1") is None
    store.record_items("s1", [_item("a")])
    assert store.last_fetched_at("s1") is not None


def test_baseline_tracked_per_source(tmp_path):
    store = Store(tmp_path)
    store.record_items("s1", [_item("a")])
    assert store.baseline_done("s1") is True
    assert store.baseline_done("s2") is False
    # 另一条源首跑同样只建档
    result = store.record_items("s2", [_item("a")])
    assert result["baseline"] == 1
    assert result["new"] == []


def test_read_items_filters_by_source_and_since(tmp_path):
    store = Store(tmp_path)
    store.record_items("s1", [_item("a")])
    store.record_items("s2", [_item("x")])
    store.record_items("s1", [_item("b")])
    store.record_items("s2", [_item("y")])

    s1_items = store.read_items(source_id="s1")
    assert [i.item_id for i in s1_items] == ["b"]

    from datetime import datetime, timedelta, timezone

    recent = store.read_items(since=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert {i.item_id for i in recent} == {"b", "y"}
    future = store.read_items(since=datetime.now(timezone.utc) + timedelta(minutes=1))
    assert future == []


def test_read_items_ignores_corrupt_lines(tmp_path):
    store = Store(tmp_path)
    store.record_items("s1", [_item("a")])
    store.record_items("s1", [_item("b")])
    with open(tmp_path / "data" / "s1.jsonl", "a", encoding="utf-8") as handle:
        handle.write("not-json\n")
    assert [i.item_id for i in store.read_items(source_id="s1")] == ["b"]
