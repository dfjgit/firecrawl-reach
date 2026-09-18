# -*- coding: utf-8 -*-
"""twitter-cli 适配器：命令构造、凭据注入、JSON 解析、认证失效识别。"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from acquisition.adapters import twitter_cli
from acquisition.adapters.base import (
    AdapterError,
    LoginInvalidError,
    LoginRequiredError,
)
from acquisition.sources import Source


def _source(**overrides):
    base = {
        "id": "x-timeline-trump",
        "name": "Trump on X",
        "lane": "auth",
        "kind": "twitter-cli",
        "command": ["user-posts", "@realDonaldTrump", "-n", "20"],
        "extra_args": {"jitter": False},  # 过 router 的用例不真睡
    }
    base.update(overrides)
    return Source(**base)


def _proc(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


@pytest.fixture
def installed(monkeypatch):
    """twitter 可执行文件存在。"""
    monkeypatch.setattr(twitter_cli.shutil, "which", lambda name: "/usr/local/bin/twitter")


@pytest.fixture
def creds(monkeypatch):
    """配置里有一对显式凭据；当前进程环境干净。"""
    monkeypatch.delenv("TWITTER_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_CT0", raising=False)
    config = Mock()
    config.get.side_effect = lambda key, default=None: {
        "twitter_auth_token": "saved-auth",
        "twitter_ct0": "saved-ct0",
    }.get(key, default)
    monkeypatch.setattr(twitter_cli, "Config", lambda read_only=True: config)
    return config


TWEETS = [
    {
        "id": "18001",
        "text": "第一条推文",
        "created_at": "Mon Aug 03 08:00:00 +0000 2026",
        "favorite_count": 123,
        "retweet_count": 45,
        "username": "realDonaldTrump",
    },
    {
        "id_str": "18002",
        "full_text": "第二条推文",
        "created_at": "Mon Aug 03 07:00:00 +0000 2026",
        "url": "https://x.com/realDonaldTrump/status/18002",
    },
]


# ── 前置检查 ──────────────────────────────────────


def test_not_installed_raises_adapter_error(monkeypatch):
    monkeypatch.setattr(twitter_cli.shutil, "which", lambda name: None)
    with pytest.raises(AdapterError, match="pipx install"):
        twitter_cli.TwitterCliAdapter().fetch(_source())


def test_missing_credentials_raise_login_required(installed, monkeypatch):
    monkeypatch.delenv("TWITTER_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_CT0", raising=False)
    config = Mock()
    config.get.side_effect = lambda key, default=None: default
    monkeypatch.setattr(twitter_cli, "Config", lambda read_only=True: config)
    with pytest.raises(LoginRequiredError, match="Cookie-Editor"):
        twitter_cli.TwitterCliAdapter().fetch(_source())


def test_empty_command_raises_adapter_error(installed, creds):
    with pytest.raises(AdapterError, match="command"):
        twitter_cli.TwitterCliAdapter().fetch(_source(command=[]))


# ── 命令构造与凭据注入 ──────────────────────────────


def test_command_appends_json_and_injects_env(installed, creds, monkeypatch):
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _proc(stdout=json.dumps(TWEETS))

    adapter = twitter_cli.TwitterCliAdapter(runner=runner)
    items = adapter.fetch(_source())

    assert captured["cmd"][:2] == ["twitter", "user-posts"]
    assert captured["cmd"][-1] == "--json"
    # 凭据进入子进程环境，但不污染当前进程
    assert captured["env"]["TWITTER_AUTH_TOKEN"] == "saved-auth"
    assert captured["env"]["TWITTER_CT0"] == "saved-ct0"
    import os

    assert "TWITTER_AUTH_TOKEN" not in os.environ
    assert len(items) == 2


def test_command_keeps_existing_yaml_flag(installed, creds):
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        return _proc(stdout="not-json", returncode=1)

    adapter = twitter_cli.TwitterCliAdapter(runner=runner)
    with pytest.raises(AdapterError):
        adapter.fetch(_source(command=["user-posts", "@x", "--yaml"]))
    assert "--json" not in captured["cmd"]


# ── 解析与映射 ────────────────────────────────────


def test_maps_tweet_fields(installed, creds):
    def runner(cmd, **kwargs):
        return _proc(stdout=json.dumps(TWEETS))

    items = twitter_cli.TwitterCliAdapter(runner=runner).fetch(_source())
    first, second = items

    assert first.platform == "twitter"
    assert first.item_id == "18001"
    assert first.content == "第一条推文"
    assert first.author == "Trump on X"
    assert first.url == "https://x.com/realDonaldTrump/status/18001"
    assert first.metrics == {"liked": "123", "retweet": "45"}
    assert second.item_id == "18002"
    assert second.content == "第二条推文"
    assert second.url == "https://x.com/realDonaldTrump/status/18002"


def test_parses_wrapper_dict(installed, creds):
    payload = {"tweets": TWEETS[:1]}

    def runner(cmd, **kwargs):
        return _proc(stdout=json.dumps(payload))

    items = twitter_cli.TwitterCliAdapter(runner=runner).fetch(_source())
    assert len(items) == 1
    assert items[0].item_id == "18001"


def test_parses_single_tweet_object(installed, creds):
    def runner(cmd, **kwargs):
        return _proc(stdout=json.dumps(TWEETS[0]))

    items = twitter_cli.TwitterCliAdapter(runner=runner).fetch(_source())
    assert len(items) == 1


# ── 失效与错误 ────────────────────────────────────


def test_auth_error_on_nonzero_exit(installed, creds):
    def runner(cmd, **kwargs):
        return _proc(stderr="Error: 401 Unauthorized - auth_token expired", returncode=1)

    with pytest.raises(LoginInvalidError, match="凭据失效"):
        twitter_cli.TwitterCliAdapter(runner=runner).fetch(_source())


def test_generic_error_on_nonzero_exit(installed, creds):
    def runner(cmd, **kwargs):
        return _proc(stderr="GraphQL 404 Not Found", returncode=1)

    with pytest.raises(AdapterError, match="退出码 1"):
        twitter_cli.TwitterCliAdapter(runner=runner).fetch(_source())


def test_non_json_output_with_auth_marker(installed, creds):
    def runner(cmd, **kwargs):
        return _proc(stdout="please login first")

    with pytest.raises(LoginInvalidError):
        twitter_cli.TwitterCliAdapter(runner=runner).fetch(_source())


def test_non_json_output_without_marker(installed, creds):
    def runner(cmd, **kwargs):
        return _proc(stdout="<html>unexpected</html>")

    with pytest.raises(AdapterError, match="JSON"):
        twitter_cli.TwitterCliAdapter(runner=runner).fetch(_source())


# ── 路由器集成 ────────────────────────────────────


def test_router_registers_twitter_cli():
    from acquisition.router import ADAPTERS

    assert "twitter-cli" in ADAPTERS


def test_router_marks_login_invalid_as_blocked(
    installed, creds, monkeypatch, tmp_path
):
    from acquisition import health, router
    from acquisition.router import run_round
    from acquisition.store import Store

    monkeypatch.setattr(health, "blocked_path", lambda: tmp_path / "blocked.json")

    def runner(cmd, **kwargs):
        return _proc(stderr="401 Unauthorized", returncode=1)

    monkeypatch.setitem(
        router.ADAPTERS,
        "twitter-cli",
        twitter_cli.TwitterCliAdapter(runner=runner).fetch,
    )

    store = Store(root=tmp_path)
    try:
        summaries = run_round([_source()], store)
    finally:
        store.close()

    assert summaries[0]["status"] == "blocked"
    assert health.blocked_sources() == {"x-timeline-trump"}
