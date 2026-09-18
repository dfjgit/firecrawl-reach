# -*- coding: utf-8 -*-
"""Twitter/X 适配器（通道二）：子进程调用 twitter-cli。

凭据策略与 agent-reach 一致：只用 Cookie-Editor 手工导出并保存到
``~/.agent-reach/config.yaml`` 的 ``twitter_auth_token``/``twitter_ct0``，
经 ``twitter_cli_child_env`` 注入子进程环境，绝不自动读取浏览器 Cookie，
也不修改当前进程环境。

输出解析：强制附加 ``--json``（twitter-cli v0.8.5+ 支持结构化输出），
对顶层 list 或常见包裹键（tweets/items/results/data）做宽容解析。
"""

import json
import os
import shutil
import subprocess

from acquisition.adapters.base import (
    Adapter,
    AdapterError,
    LoginInvalidError,
    LoginRequiredError,
)
from acquisition.models import Item
from acquisition.sources import Source
from agent_reach.channels.twitter import twitter_cli_child_env
from agent_reach.config import Config

_DEFAULT_TIMEOUT = 120  # 子进程超时（秒），extra_args.timeout 可覆盖

_INSTALL_HINT = "twitter-cli 未安装。安装：pipx install twitter-cli（需 v0.8.5+）"
_CREDENTIAL_HINT = (
    "缺少 Twitter 凭据。用 Cookie-Editor 从 x.com 导出 Header String 后运行：\n"
    "  agent-reach configure twitter-cookies '<Header String>'"
)

# 返回码非 0 / 输出为空时识别登录态失效的特征（只看 stderr/stdout 文本）
_AUTH_MARKERS = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "auth_token",
    "ct0",
    "login",
    "cookie",
    "登录",
    "认证",
)

# JSON 顶层为 dict 时尝试的包裹键
_WRAPPER_KEYS = ("tweets", "items", "results", "data", "timeline")


def _ensure_ready() -> None:
    if not shutil.which("twitter"):
        raise AdapterError(_INSTALL_HINT)


def _child_env() -> dict:
    """构造子进程环境：当前环境 + 配置里缺失的显式凭据。"""
    config = Config(read_only=True)
    env = dict(os.environ)
    env.update(twitter_cli_child_env(config))
    if not env.get("TWITTER_AUTH_TOKEN") or not env.get("TWITTER_CT0"):
        raise LoginRequiredError(_CREDENTIAL_HINT)
    return env


def _build_cmd(source: Source) -> list:
    """twitter + 源 command + --json（若未指定结构化输出）。"""
    if not source.command:
        raise AdapterError(
            f"源 {source.id}：kind=twitter-cli 必须配置 command"
            '（如 ["user-posts", "@username", "-n", "20"]）'
        )
    cmd = ["twitter", *(str(part) for part in source.command)]
    if "--json" not in cmd and "--yaml" not in cmd:
        cmd.append("--json")
    return cmd


def _has_auth_marker(output: str) -> bool:
    lowered = output.lower()
    return any(marker in lowered for marker in _AUTH_MARKERS)


def _extract_entries(payload) -> list:
    """宽容解析 JSON 产物：顶层 list 直接用，dict 则找常见包裹键。"""
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]
    if isinstance(payload, dict):
        for key in _WRAPPER_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return [e for e in value if isinstance(e, dict)]
        # 单条推文对象（如 twitter tweet <id>）
        if any(k in payload for k in ("id", "id_str", "tweet_id", "rest_id")):
            return [payload]
    return []


def _first(entry: dict, *keys: str) -> str:
    for key in keys:
        value = entry.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _map_item(entry: dict, source: Source) -> Item:
    item_id = _first(entry, "id", "id_str", "tweet_id", "rest_id")
    url = _first(entry, "url", "tweet_url", "link")
    if not url and item_id:
        user = _first(entry, "username", "screen_name")
        user_prefix = f"{user.lstrip('@')}/" if user else ""
        url = f"https://x.com/{user_prefix}status/{item_id}"
    metrics = {}
    for name, keys in (
        ("liked", ("favorite_count", "like_count", "likes")),
        ("retweet", ("retweet_count", "retweets")),
        ("reply", ("reply_count", "replies")),
        ("views", ("view_count", "views")),
    ):
        value = _first(entry, *keys)
        if value:
            metrics[name] = value
    return Item(
        source_id=source.id,
        lane=source.lane,
        platform="twitter",
        item_id=item_id,
        author=source.name,
        content=_first(entry, "text", "full_text", "content"),
        url=url,
        published_at=_first(entry, "created_at", "createdAt", "date"),
        metrics=metrics,
        snapshot=False,
    )


class TwitterCliAdapter(Adapter):
    """子进程调用 twitter-cli 并解析 JSON 产物。"""

    def __init__(self, runner=None) -> None:
        # runner 可注入（测试 mock subprocess.run）
        self._runner = runner or subprocess.run

    def fetch(self, source: Source) -> list:
        _ensure_ready()
        env = _child_env()
        cmd = _build_cmd(source)
        timeout = int(source.extra_args.get("timeout", _DEFAULT_TIMEOUT))

        try:
            proc = self._runner(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterError(f"twitter-cli 超时（{timeout}s）：{source.id}") from exc

        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            if _has_auth_marker(output):
                raise LoginInvalidError(
                    f"twitter-cli 返回认证错误，判定凭据失效：{source.id}"
                )
            tail = (proc.stderr or proc.stdout or "").strip()[-500:]
            raise AdapterError(f"twitter-cli 退出码 {proc.returncode}：{tail}")

        try:
            payload = json.loads(proc.stdout or "")
        except ValueError as exc:
            if _has_auth_marker(output):
                raise LoginInvalidError(
                    f"twitter-cli 输出非 JSON 且含认证特征，判定凭据失效：{source.id}"
                ) from exc
            raise AdapterError(
                f"twitter-cli 输出无法解析为 JSON（command 是否支持 --json？）：{source.id}"
            ) from exc

        entries = _extract_entries(payload)
        return [_map_item(entry, source) for entry in entries]


def fetch(source: Source) -> list:
    """模块级便捷入口（路由器按 kind 分发用）。"""
    return TwitterCliAdapter().fetch(source)
