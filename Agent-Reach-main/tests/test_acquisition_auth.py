# -*- coding: utf-8 -*-
"""M2：health 的 blocked 写入侧、router 的 auth 通道逻辑、CLI blocked/unblock。

router 测试里 mediacrawler 适配器与渠道体检全部 mock，不真睡抖动。
"""

import json

import pytest

import acquisition.cli as cli
import acquisition.router as router
from acquisition import acquisition_dir
from acquisition.adapters.base import (
    AdapterError,
    LoginInvalidError,
    LoginRequiredError,
)
from acquisition.health import (
    block_source,
    blocked_entries,
    blocked_sources,
    unblock_source,
)
from acquisition.models import Item
from acquisition.router import run_round
from acquisition.sources import Source
from acquisition.store import Store


def _auth_source(source_id="weibo-creator-xxx", platform="wb", **kwargs):
    base = {
        "id": source_id,
        "name": source_id,
        "lane": "auth",
        "kind": "mediacrawler",
        "platform": platform,
        "crawler_type": "creator",
        "creator_id": "123",
        "interval": 300,
    }
    base.update(kwargs)
    return Source(**base)


def _items(source_id, *item_ids):
    return [
        Item(source_id=source_id, lane="auth", platform="weibo", item_id=i, content="c")
        for i in item_ids
    ]


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    """体检 ok + 关闭抖动 + 可替换的 mediacrawler 适配器。"""
    monkeypatch.setattr(router, "_channel_check", lambda name: ("ok", "就绪"))
    monkeypatch.setattr(router.time, "sleep", lambda seconds: None)
    return Store(tmp_path)


# ── health 写入侧 ─────────────────────────────────


def test_block_source_writes_and_reads(tmp_path):
    path = tmp_path / "blocked.json"
    block_source("s1", "登录态失效", platform="wb", path=path)
    assert blocked_sources(path) == {"s1"}
    (entry,) = blocked_entries(path)
    assert entry["source_id"] == "s1"
    assert entry["reason"] == "登录态失效"
    assert entry["platform"] == "wb"
    assert entry["blocked_at"]
    # 落盘格式是 {"blocked": [...]}
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload["blocked"], list)


def test_block_source_replaces_same_id(tmp_path):
    path = tmp_path / "blocked.json"
    block_source("s1", "原因一", path=path)
    block_source("s1", "原因二", path=path)
    assert len(blocked_entries(path)) == 1
    assert blocked_entries(path)[0]["reason"] == "原因二"


def test_unblock_source(tmp_path):
    path = tmp_path / "blocked.json"
    block_source("s1", "r", path=path)
    block_source("s2", "r", path=path)
    assert unblock_source("s1", path=path) is True
    assert blocked_sources(path) == {"s2"}
    assert unblock_source("s1", path=path) is False


def test_block_then_read_compat_with_old_list_format(tmp_path):
    path = tmp_path / "blocked.json"
    path.write_text(json.dumps(["legacy"]), encoding="utf-8")
    assert blocked_sources(path) == {"legacy"}
    block_source("s1", "r", path=path)
    assert blocked_sources(path) == {"legacy", "s1"}


# ── router：auth 渠道体检 ─────────────────────────


def test_auth_channel_check_not_ok_blocks_source(auth_env, monkeypatch):
    monkeypatch.setattr(router, "_channel_check", lambda name: ("warn", "未扫码登录"))
    fetched = []
    monkeypatch.setitem(router.ADAPTERS, "mediacrawler", lambda s: fetched.append(s.id) or [])
    (entry,) = run_round([_auth_source()], auth_env)
    assert entry["status"] == "blocked"
    assert "未扫码登录" in entry["error"]
    assert fetched == []  # 体检不过就不抓
    # 写进了 blocked.json（默认路径在隔离 HOME 下）
    assert "weibo-creator-xxx" in blocked_sources()
    (blocked_entry,) = blocked_entries()
    assert blocked_entry["platform"] == "wb"
    assert blocked_entry["reason"] == "未扫码登录"


def test_channel_check_cached_per_round(auth_env, monkeypatch):
    calls = []
    monkeypatch.setattr(
        router, "_channel_check", lambda name: calls.append(name) or ("ok", "就绪")
    )
    monkeypatch.setitem(router.ADAPTERS, "mediacrawler", lambda s: _items(s.id, "a"))
    sources = [_auth_source("w1"), _auth_source("w2")]
    run_round(sources, auth_env)
    assert calls == ["weibo"]  # 同渠道一轮只查一次


def test_login_required_error_blocks_source(auth_env, monkeypatch):
    def fake_fetch(source):
        raise LoginRequiredError("平台 wb 无登录态缓存")

    monkeypatch.setitem(router.ADAPTERS, "mediacrawler", fake_fetch)
    (entry,) = run_round([_auth_source()], auth_env)
    assert entry["status"] == "blocked"
    assert "无登录态缓存" in entry["error"]
    assert "weibo-creator-xxx" in blocked_sources()


def test_login_invalid_error_blocks_source(auth_env, monkeypatch):
    def fake_fetch(source):
        raise LoginInvalidError("产物为空且日志含登录/验证特征")

    monkeypatch.setitem(router.ADAPTERS, "mediacrawler", fake_fetch)
    (entry,) = run_round([_auth_source()], auth_env)
    assert entry["status"] == "blocked"
    assert "weibo-creator-xxx" in blocked_sources()


def test_plain_adapter_error_is_error_not_blocked(auth_env, monkeypatch):
    def fake_fetch(source):
        raise AdapterError("MediaCrawler 退出码 2")

    monkeypatch.setitem(router.ADAPTERS, "mediacrawler", fake_fetch)
    (entry,) = run_round([_auth_source()], auth_env)
    assert entry["status"] == "error"
    assert blocked_sources() == set()


# ── router：串行 + 抖动 ────────────────────────────


def test_auth_sources_run_serially_with_jitter(tmp_path, monkeypatch):
    monkeypatch.setattr(router, "_channel_check", lambda name: ("ok", "就绪"))
    sleeps = []
    monkeypatch.setattr(router.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(router.random, "uniform", lambda a, b: 2.0)
    events = []
    monkeypatch.setitem(
        router.ADAPTERS, "mediacrawler", lambda s: events.append(s.id) or _items(s.id, "a")
    )
    store = Store(tmp_path)
    sources = [_auth_source("w1"), _auth_source("w2"), _auth_source("w3")]
    summaries = run_round(sources, store)
    assert events == ["w1", "w2", "w3"]  # 串行按序
    assert sleeps == [2.0, 2.0, 2.0]  # 每个源前都有抖动
    assert all(e["status"] == "ok" for e in summaries)


def test_jitter_disabled_via_extra_args(tmp_path, monkeypatch):
    monkeypatch.setattr(router, "_channel_check", lambda name: ("ok", "就绪"))
    sleeps = []
    monkeypatch.setattr(router.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setitem(router.ADAPTERS, "mediacrawler", lambda s: _items(s.id, "a"))
    store = Store(tmp_path)
    run_round([_auth_source(extra_args={"jitter": False})], store)
    assert sleeps == []


def test_auth_ok_flow_records_items(tmp_path, monkeypatch):
    monkeypatch.setattr(router, "_channel_check", lambda name: ("ok", "就绪"))
    monkeypatch.setattr(router.time, "sleep", lambda seconds: None)
    monkeypatch.setitem(router.ADAPTERS, "mediacrawler", lambda s: _items(s.id, "a", "b"))
    store = Store(tmp_path)
    first = run_round([_auth_source()], store)
    assert first[0]["status"] == "ok"
    assert first[0]["baseline"] == 2  # 首跑只建档
    second = run_round([_auth_source()], store, force=True)
    assert second[0]["dup"] == 2


# ── CLI：blocked / unblock ────────────────────────


def test_cli_blocked_lists_entries(capsys):
    block_source("weibo-creator-xxx", "未扫码登录", platform="wb")
    assert cli.main(["blocked"]) == 0
    out = capsys.readouterr().out
    assert "weibo-creator-xxx" in out
    assert "platform=wb" in out
    assert "未扫码登录" in out


def test_cli_blocked_empty(capsys):
    assert cli.main(["blocked"]) == 0
    assert "无 blocked 源" in capsys.readouterr().out


def test_cli_unblock(capsys):
    block_source("s1", "r", platform="wb")
    assert cli.main(["unblock", "s1"]) == 0
    assert "已解除 blocked：s1" in capsys.readouterr().out
    assert blocked_sources() == set()
    assert cli.main(["unblock", "s1"]) == 0
    assert "不在 blocked 列表" in capsys.readouterr().out


def test_cli_run_blocked_source_stays_blocked(tmp_path, monkeypatch, capsys):
    """已在 blocked.json 里的源 run 时直接记 blocked（M1 已有行为，M2 回归）。"""
    block_source("weibo-creator-xxx", "r", platform="wb")
    sources_yaml = tmp_path / "sources.yaml"
    sources_yaml.write_text(
        "- id: weibo-creator-xxx\n"
        "  name: 某财经博主\n"
        "  lane: auth\n"
        "  kind: mediacrawler\n"
        "  platform: wb\n"
        "  crawler_type: creator\n"
        "  creator_id: '123'\n",
        encoding="utf-8",
    )
    assert cli.main(["run", "--sources", str(sources_yaml)]) == 0
    assert "weibo-creator-xxx → blocked" in capsys.readouterr().out
    assert acquisition_dir().joinpath("blocked.json").is_file()
