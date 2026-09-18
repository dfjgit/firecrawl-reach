# -*- coding: utf-8 -*-
"""路由器：节流 skipped、blocked、适配器未就绪、并发抓取不互相影响。"""

from datetime import datetime, timedelta, timezone

from acquisition.models import Item
from acquisition.router import run_round
from acquisition.sources import Source
from acquisition.store import Store


def _source(source_id, kind="rss", lane="news", interval=300, **kwargs):
    return Source(
        id=source_id,
        name=source_id,
        lane=lane,
        kind=kind,
        url=f"https://example.com/{source_id}",
        interval=interval,
        **kwargs,
    )


def _items(source_id, *item_ids):
    return [
        Item(source_id=source_id, lane="news", platform="rss", item_id=i, content=f"c{i}")
        for i in item_ids
    ]


def test_first_run_baseline_then_dup(tmp_path, monkeypatch):
    import acquisition.router as router

    monkeypatch.setitem(router.ADAPTERS, "rss", lambda s: _items(s.id, "a", "b"))
    store = Store(tmp_path)
    first = run_round([_source("s1")], store)
    assert first[0]["status"] == "ok"
    assert first[0]["new"] == 0  # baseline 只建档
    assert first[0]["baseline"] == 2

    second = run_round([_source("s1")], store, force=True)
    assert second[0]["new"] == 0
    assert second[0]["dup"] == 2


def test_throttle_skips_recent_source(tmp_path, monkeypatch):
    import acquisition.router as router

    monkeypatch.setitem(router.ADAPTERS, "rss", lambda s: _items(s.id, "a"))
    store = Store(tmp_path)
    run_round([_source("s1", interval=300)], store)
    # 立刻再跑：距上次不足 interval → skipped
    skipped = run_round([_source("s1", interval=300)], store)
    assert skipped[0]["status"] == "skipped"
    assert "节流" in skipped[0]["error"]
    # --force 覆盖节流
    forced = run_round([_source("s1", interval=300)], store, force=True)
    assert forced[0]["status"] == "ok"


def test_throttle_uses_stored_timestamp(tmp_path, monkeypatch):
    import acquisition.router as router

    called = []
    monkeypatch.setitem(router.ADAPTERS, "rss", lambda s: called.append(s.id) or [])
    store = Store(tmp_path)
    store.record_items("s1", _items("s1", "a"))
    now = datetime.now(timezone.utc)
    # 模拟"上次抓取已在 interval 之外"
    old = now - timedelta(seconds=600)
    monkeypatch.setattr(store, "last_fetched_at", lambda sid: old)
    run_round([_source("s1", interval=300)], store, now=now)
    assert called == ["s1"]


def test_auth_lane_kind_without_adapter_marks_error(tmp_path):
    store = Store(tmp_path)
    source = _source("x1", kind="jina", lane="auth")
    (entry,) = run_round([source], store)
    assert entry["status"] == "error"
    assert "未知 kind" in entry["error"]


def test_news_lane_kind_without_adapter_marks_error(tmp_path):
    store = Store(tmp_path)
    source = _source("m1", kind="jina", lane="news")
    (entry,) = run_round([source], store)
    assert entry["status"] == "error"
    assert "未知 kind" in entry["error"]


def test_blocked_source_skipped(tmp_path):
    store = Store(tmp_path)
    (entry,) = run_round([_source("s1")], store, blocked={"s1"})
    assert entry["status"] == "blocked"


def test_disabled_source_skipped(tmp_path):
    store = Store(tmp_path)
    (entry,) = run_round([_source("s1", enabled=False)], store)
    assert entry["status"] == "skipped"


def test_one_source_failure_does_not_crash_others(tmp_path, monkeypatch):
    import acquisition.router as router
    from acquisition.adapters.base import AdapterError

    def fake_fetch(source):
        if source.id == "bad":
            raise AdapterError("炸了")
        return _items(source.id, "a")

    monkeypatch.setitem(router.ADAPTERS, "rss", fake_fetch)
    store = Store(tmp_path)
    summaries = run_round([_source("ok1"), _source("bad"), _source("ok2")], store)
    by_id = {e["source_id"]: e for e in summaries}
    assert by_id["ok1"]["status"] == "ok"
    assert by_id["ok2"]["status"] == "ok"
    assert by_id["bad"]["status"] == "error"
    assert "炸了" in by_id["bad"]["error"]
    # 顺序与源顺序一致
    assert [e["source_id"] for e in summaries] == ["ok1", "bad", "ok2"]
