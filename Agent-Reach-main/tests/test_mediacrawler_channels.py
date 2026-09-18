# -*- coding: utf-8 -*-
"""MediaCrawler 渠道：URL 匹配、doctor 状态机、子进程命令构造。"""

import pytest

from agent_reach import mediacrawler_bridge as bridge
from agent_reach.channels._mediacrawler import (
    DouyinChannel,
    KuaishouChannel,
    TiebaChannel,
    WeiboChannel,
    ZhihuChannel,
)

ALL_CHANNELS = [DouyinChannel, KuaishouChannel, WeiboChannel, TiebaChannel, ZhihuChannel]


# ── can_handle ──────────────────────────────────────


@pytest.mark.parametrize(
    "channel_cls,url,expected",
    [
        (DouyinChannel, "https://www.douyin.com/video/123", True),
        (DouyinChannel, "https://douyin.com.evil.test/", False),
        (KuaishouChannel, "https://www.kuaishou.com/short-video/abc", True),
        (WeiboChannel, "https://weibo.com/u/12345", True),
        (WeiboChannel, "https://m.weibo.cn/status/1", True),
        (TiebaChannel, "https://tieba.baidu.com/p/123", True),
        (TiebaChannel, "https://baidu.com/p/123", False),
        (ZhihuChannel, "https://www.zhihu.com/people/foo", True),
        (ZhihuChannel, "https://zhihu.com.evil.test/people/foo", False),
    ],
)
def test_can_handle(channel_cls, url, expected):
    assert channel_cls().can_handle(url) is expected


# ── check() 状态机 ──────────────────────────────────


def test_check_off_when_subtree_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "MEDIACRAWLER_DIR", tmp_path / "nope")
    channel = DouyinChannel()
    status, message = channel.check()
    assert status == "off"
    assert channel.active_backend is None


def test_check_warn_when_deps_missing(monkeypatch, tmp_path):
    (tmp_path / "main.py").write_text("# stub")
    monkeypatch.setattr(bridge, "MEDIACRAWLER_DIR", tmp_path)
    monkeypatch.setattr(bridge, "deps_ready", lambda: False)
    channel = DouyinChannel()
    status, message = channel.check()
    assert status == "warn"
    assert "uv venv" in message
    assert channel.active_backend is None


def test_check_warn_when_not_logged_in(monkeypatch, tmp_path):
    (tmp_path / "main.py").write_text("# stub")
    monkeypatch.setattr(bridge, "MEDIACRAWLER_DIR", tmp_path)
    monkeypatch.setattr(bridge, "deps_ready", lambda: True)
    channel = WeiboChannel()
    status, message = channel.check()
    assert status == "warn"
    assert "扫码登录" in message
    assert channel.active_backend == "MediaCrawler"


def test_check_ok_with_login_state(monkeypatch, tmp_path):
    (tmp_path / "main.py").write_text("# stub")
    state_dir = tmp_path / "browser_data" / "wb_user_data_dir"
    state_dir.mkdir(parents=True)
    (state_dir / "Default").mkdir()
    monkeypatch.setattr(bridge, "MEDIACRAWLER_DIR", tmp_path)
    monkeypatch.setattr(bridge, "deps_ready", lambda: True)
    channel = WeiboChannel()
    status, message = channel.check()
    assert status == "ok"
    assert "登录态已缓存" in message
    assert channel.active_backend == "MediaCrawler"


def test_empty_login_state_dir_counts_as_logged_out(monkeypatch, tmp_path):
    (tmp_path / "browser_data" / "dy_user_data_dir").mkdir(parents=True)
    monkeypatch.setattr(bridge, "MEDIACRAWLER_DIR", tmp_path)
    assert bridge.has_login_state("dy") is False


# ── 命令构造 ────────────────────────────────────────


def test_build_command_uses_venv_python(monkeypatch, tmp_path):
    venv_py = tmp_path / ".venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("")
    (tmp_path / "main.py").write_text("# stub")
    monkeypatch.setattr(bridge, "MEDIACRAWLER_DIR", tmp_path)
    monkeypatch.setattr(bridge.os, "name", "nt")

    cmd = bridge.build_command("dy", ["--type", "creator"])
    assert cmd == [
        str(venv_py),
        "main.py",
        "--platform",
        "dy",
        "--type",
        "creator",
    ]


def test_build_command_falls_back_to_current_python(monkeypatch, tmp_path):
    (tmp_path / "main.py").write_text("# stub")
    monkeypatch.setattr(bridge, "MEDIACRAWLER_DIR", tmp_path)
    monkeypatch.setattr(bridge.os, "name", "nt")

    cmd = bridge.build_command("wb")
    assert cmd[0] == bridge.sys.executable
    assert cmd[1:] == ["main.py", "--platform", "wb"]


def test_deps_ready_requires_key_packages(monkeypatch, tmp_path):
    venv_py = tmp_path / ".venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("")
    site = tmp_path / ".venv" / "Lib" / "site-packages"
    site.mkdir(parents=True)
    monkeypatch.setattr(bridge, "MEDIACRAWLER_DIR", tmp_path)
    monkeypatch.setattr(bridge.os, "name", "nt")

    assert bridge.deps_ready() is False
    for pkg in ("playwright", "typer", "httpx"):
        (site / pkg).mkdir()
    assert bridge.deps_ready() is True
