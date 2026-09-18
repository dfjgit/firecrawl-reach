# -*- coding: utf-8 -*-
"""MediaCrawler 适配器：命令构造、前置检查、产物解析、字段映射、失效识别。

全部 mock：bridge 函数 / subprocess runner / 产物文件（tmp_path 造假
data/<platform>/jsonl/），不做真实抓取。
"""

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

import acquisition.adapters.mediacrawler as mc
from acquisition.adapters.base import (
    AdapterError,
    LoginInvalidError,
    LoginRequiredError,
)
from acquisition.sources import Source


def _source(**overrides):
    base = {
        "id": "weibo-creator-xxx",
        "name": "某财经博主",
        "lane": "auth",
        "kind": "mediacrawler",
        "platform": "wb",
        "crawler_type": "creator",
        "creator_id": "1234567890",
        "max_items": 20,
    }
    base.update(overrides)
    return Source(**base)


@pytest.fixture
def ready_bridge(monkeypatch, tmp_path):
    """把 bridge 垫成"依赖就绪 + 已登录"，MEDIACRAWLER_DIR 指到 tmp_path。"""
    monkeypatch.setattr(mc.bridge, "MEDIACRAWLER_DIR", tmp_path)
    monkeypatch.setattr(mc.bridge, "mediacrawler_available", lambda: True)
    monkeypatch.setattr(mc.bridge, "deps_ready", lambda: True)
    monkeypatch.setattr(mc.bridge, "has_login_state", lambda platform: True)
    return tmp_path


def _products_file(mc_dir, writer_dir, crawler_type="creator"):
    today = datetime.now().strftime("%Y-%m-%d")
    path = mc_dir / "data" / writer_dir / "jsonl" / f"{crawler_type}_contents_{today}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _runner_writing(path, lines, stdout="ok", stderr="", returncode=0):
    """造一个假 subprocess.run：把 lines 追加进产物文件后返回给定结果。"""
    def fake_run(cmd, **kwargs):
        if returncode == 0 and lines:
            with open(path, "a", encoding="utf-8") as handle:
                for line in lines:
                    handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return fake_run


WB_NOTE = {
    "note_id": "5123456789",
    "content": "今天聊聊财政政策",
    "create_time": 1785000000,
    "create_date_time": "2026-08-03 12:00:00",
    "liked_count": "12",
    "comments_count": "3",
    "shared_count": "1",
    "last_modify_ts": 1785000001,
    "note_url": "https://m.weibo.cn/detail/5123456789",
    "creator_hash": "ab12cd34ef56",  # 匿名化字段：不映射
    "nickname": "某**主",  # 脱敏昵称：不映射
    "source_keyword": "",
}

DY_AWEME = {
    "aweme_id": "7300000000000000001",
    "aweme_type": "0",
    "title": "盘后解读",
    "desc": "今日盘面三个要点",
    "create_time": 1785000000,
    "creator_hash": "ff00ff00ff00",
    "nickname": "某**者",
    "liked_count": "100",
    "collected_count": "20",
    "comment_count": "30",
    "share_count": "5",
    "last_modify_ts": 1785000002,
    "aweme_url": "https://www.douyin.com/video/7300000000000000001",
    "cover_url": "https://example.com/cover.jpg",
    "video_download_url": "",
    "music_download_url": "",
    "note_download_url": "",
    "source_keyword": "",
}


# ── 命令构造 ──────────────────────────────────────


def test_build_args_creator_mode():
    args = mc._build_args(_source())
    assert args[:2] == ["--type", "creator"]
    assert "--creator_id" in args
    assert args[args.index("--creator_id") + 1] == "1234567890"
    assert args[args.index("--crawler_max_notes_count") + 1] == "20"
    # 默认不抓评论
    assert args[args.index("--get_comment") + 1] == "no"
    assert args[args.index("--save_data_option") + 1] == "jsonl"


def test_build_args_search_mode_keywords():
    source = _source(
        crawler_type="search", creator_id="", extra_args={"keywords": "财政,央行"}
    )
    args = mc._build_args(source)
    assert args[:2] == ["--type", "search"]
    assert args[args.index("--keywords") + 1] == "财政,央行"
    assert "--creator_id" not in args


def test_build_args_get_comment_overridable():
    source = _source(extra_args={"get_comment": "yes"})
    args = mc._build_args(source)
    assert args[args.index("--get_comment") + 1] == "yes"


def test_build_args_extra_passthrough_and_reserved_keys():
    source = _source(
        extra_args={
            "headless": "no",
            "start": 2,
            "timeout": 60,  # 保留键：不透传
            "jitter": False,  # 保留键：不透传
        }
    )
    args = mc._build_args(source)
    assert args[args.index("--headless") + 1] == "no"
    assert args[args.index("--start") + 1] == "2"
    assert "--timeout" not in args
    assert "--jitter" not in args


def test_fetch_command_uses_bridge_build_command(ready_bridge):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    source = _source()
    mc.MediaCrawlerAdapter(runner=fake_run).fetch(source)
    assert captured["cmd"][1] == "main.py"
    assert captured["cmd"][2:4] == ["--platform", "wb"]
    assert "--creator_id" in captured["cmd"]
    assert captured["cwd"] == str(ready_bridge)


# ── 前置检查 ──────────────────────────────────────


def test_fetch_raises_when_subtree_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mc.bridge, "MEDIACRAWLER_DIR", tmp_path)
    monkeypatch.setattr(mc.bridge, "mediacrawler_available", lambda: False)
    with pytest.raises(AdapterError, match="INSTALL_HINT|uv venv"):
        mc.MediaCrawlerAdapter(runner=lambda *a, **k: None).fetch(_source())


def test_fetch_raises_when_deps_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mc.bridge, "MEDIACRAWLER_DIR", tmp_path)
    monkeypatch.setattr(mc.bridge, "mediacrawler_available", lambda: True)
    monkeypatch.setattr(mc.bridge, "deps_ready", lambda: False)
    with pytest.raises(AdapterError, match="uv venv"):
        mc.MediaCrawlerAdapter(runner=lambda *a, **k: None).fetch(_source())


def test_fetch_raises_login_required_without_login_state(ready_bridge, monkeypatch):
    monkeypatch.setattr(mc.bridge, "has_login_state", lambda platform: False)
    with pytest.raises(LoginRequiredError, match="agent-reach crawl wb --lt qrcode"):
        mc.MediaCrawlerAdapter(runner=lambda *a, **k: None).fetch(_source())


def test_fetch_unknown_platform_raises(ready_bridge):
    with pytest.raises(AdapterError, match="未知 mediacrawler 平台码"):
        mc.MediaCrawlerAdapter(runner=lambda *a, **k: None).fetch(_source(platform="xyz"))


# ── 子进程结果处理 ────────────────────────────────


def test_fetch_nonzero_exit_raises_with_stderr_tail(ready_bridge):
    runner = _runner_writing(None, [], stderr="x" * 600 + "致命错误尾部", returncode=2)
    with pytest.raises(AdapterError, match="退出码 2"):
        mc.MediaCrawlerAdapter(runner=runner).fetch(_source())


def test_fetch_timeout_raises(ready_bridge):
    import subprocess

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=900)

    with pytest.raises(AdapterError, match="超时"):
        mc.MediaCrawlerAdapter(runner=fake_run).fetch(_source())


def test_fetch_empty_products_with_login_marker_raises_invalid(ready_bridge):
    runner = _runner_writing(
        None, [], stdout="[DouYinLogin.begin] login failed please confirm ..."
    )
    with pytest.raises(LoginInvalidError, match="登录态失效"):
        mc.MediaCrawlerAdapter(runner=runner).fetch(_source())


def test_fetch_empty_products_without_marker_returns_empty(ready_bridge):
    runner = _runner_writing(None, [], stdout="crawl finished, no new notes")
    assert mc.MediaCrawlerAdapter(runner=runner).fetch(_source()) == []


# ── 字段映射 ──────────────────────────────────────


def test_weibo_field_mapping(ready_bridge):
    products = _products_file(ready_bridge, "weibo")
    runner = _runner_writing(products, [WB_NOTE])
    (item,) = mc.MediaCrawlerAdapter(runner=runner).fetch(_source())
    assert item.item_id == "5123456789"
    assert item.platform == "weibo"
    assert item.author == "某财经博主"  # 归属从源注册表恢复
    assert item.content == "今天聊聊财政政策"
    assert item.published_at == "2026-08-03 12:00:00"
    assert item.url == "https://m.weibo.cn/detail/5123456789"
    assert item.metrics == {"liked": "12", "comments": "3", "shared": "1"}
    assert item.source_id == "weibo-creator-xxx"
    assert item.lane == "auth"
    assert item.snapshot is False
    # 匿名化/脱敏字段不进入 Item（Item 模型根本没有这些字段）
    assert "creator_hash" not in item.to_dict()
    assert "nickname" not in item.to_dict()


def test_douyin_field_mapping(ready_bridge):
    products = _products_file(ready_bridge, "douyin")
    runner = _runner_writing(products, [DY_AWEME])
    source = _source(id="douyin-creator-yyy", platform="dy", creator_id="<sec_uid>")
    (item,) = mc.MediaCrawlerAdapter(runner=runner).fetch(source)
    assert item.item_id == "7300000000000000001"
    assert item.platform == "douyin"
    assert item.title == "盘后解读"
    assert item.content == "今日盘面三个要点"
    assert item.published_at == "1785000000"
    assert item.url == "https://www.douyin.com/video/7300000000000000001"
    assert item.metrics == {"liked": "100", "collected": "20", "comments": "30", "shared": "5"}


def test_only_appended_lines_are_read(ready_bridge):
    products = _products_file(ready_bridge, "weibo")
    # 同一天早些时候的旧条目：偏移量之前，不应被本次读到
    old = dict(WB_NOTE, note_id="old-note")
    products.write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
    runner = _runner_writing(products, [WB_NOTE])
    items = mc.MediaCrawlerAdapter(runner=runner).fetch(_source())
    assert [i.item_id for i in items] == ["5123456789"]


def test_corrupt_lines_skipped(ready_bridge):
    products = _products_file(ready_bridge, "weibo")

    def fake_run(cmd, **kwargs):
        with open(products, "a", encoding="utf-8") as handle:
            handle.write("not-json\n")
            handle.write(json.dumps(WB_NOTE, ensure_ascii=False) + "\n")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    items = mc.MediaCrawlerAdapter(runner=fake_run).fetch(_source())
    assert len(items) == 1
