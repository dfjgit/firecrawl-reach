# -*- coding: utf-8 -*-
"""stealth 适配器（L1/L2）：封装 StealthBackend.read 渲染兜底。

渲染出可读文本后，源配了 ``extra_args.item_pattern`` 正则就走 L1 规则
切分；切不动或没配规则就 L2 整页快照兜底（一条 snapshot 条目）。

backend 必须带真实 Config（``stealth_ja3``/``proxy``/``stealth_timeout``
/站点 Cookie 都由它控制）——裸 ``StealthBackend()`` 用 _NullConfig，
``stealth_ja3`` 恒为默认开启，无 Go sidecar 的机器上所有 stealth 源
会直接报 StealthUnavailableError。
"""

import re
from datetime import datetime, timezone, timedelta

from acquisition.adapters.base import Adapter, AdapterError
from acquisition.extractors import make_snapshot, split_by_pattern
from acquisition.models import Item
from acquisition.sources import Source
from agent_reach.backends.stealth import (
    StealthBackend,
    StealthReadError,
    StealthUnavailableError,
)
from agent_reach.config import Config

_DEFAULT_SNAPSHOT_CHARS = 4000

# 电报时间戳：正文开头 "11:09:47" / "11:09"（财联社电报正文自带发布时间）
_TELEGRAPH_TS = re.compile(r"^\s*([01]\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?\s*")

# 财联社等中文电报时间戳为北京时间（UTC+8）
_CST = timezone(timedelta(hours=8))


def _chunk_title(chunk: str) -> str:
    """L1 条目的标题：取首行截断 80 字符（下游按标题去重/展示用）。"""
    first_line = chunk.split("\n", 1)[0].strip()
    return first_line[:80]


def _chunk_published_at(chunk: str) -> str:
    """从电报正文开头提取发布时间（UTC ISO）。

    财联社电报正文格式：'11:09:47财联社8月3日电，...'——时间戳在开头。
    时间戳为北京时间（UTC+8），转 UTC 存储。无时间戳返回空串。
    """
    m = _TELEGRAPH_TS.match(chunk)
    if not m:
        return ""
    try:
        now = datetime.now(_CST)
        return now.replace(
            hour=int(m.group(1)), minute=int(m.group(2)),
            second=int(m.group(3) or 0), microsecond=0,
        ).astimezone(timezone.utc).isoformat()
    except ValueError:
        return ""


class StealthAdapter(Adapter):
    """JS 壳页面的渲染抓取 + 三档抽取的 L1/L2 两档。"""

    def __init__(self, backend=None) -> None:
        # backend 可注入（测试 mock）；None 时每次 fetch 新建
        self._backend = backend

    def fetch(self, source: Source) -> list:
        # 必须传真实 Config：stealth_ja3/proxy/超时/站点 Cookie 都从配置读；
        # 裸 StealthBackend() 的 _NullConfig 会让 stealth_ja3 恒为默认开启
        backend = self._backend or StealthBackend(config=Config(read_only=True))
        try:
            text = backend.read(source.url)
        except (StealthUnavailableError, StealthReadError) as exc:
            raise AdapterError(str(exc)) from exc

        pattern = source.extra_args.get("item_pattern")
        if pattern:
            chunks = split_by_pattern(text, pattern, source.max_items)
            if chunks:
                return [
                    Item(
                        source_id=source.id,
                        lane=source.lane,
                        platform=source.platform or "web",
                        author=source.name,
                        title=_chunk_title(chunk),
                        content=chunk,
                        url=source.url,
                        published_at=_chunk_published_at(chunk),
                        snapshot=False,
                    )
                    for chunk in chunks
                ]

        max_chars = int(source.extra_args.get("max_chars", _DEFAULT_SNAPSHOT_CHARS))
        snapshot = make_snapshot(text, max_chars)
        if not snapshot:
            raise AdapterError(f"stealth 渲染结果为空：{source.url}")
        return [
            Item(
                source_id=source.id,
                lane=source.lane,
                platform=source.platform or "web",
                author=source.name,
                content=snapshot,
                url=source.url,
                published_at=_chunk_published_at(snapshot),
                snapshot=True,
            )
        ]


def fetch(source: Source) -> list:
    """模块级便捷入口（路由器按 kind 分发用）。"""
    return StealthAdapter().fetch(source)
