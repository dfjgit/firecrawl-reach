# -*- coding: utf-8 -*-
"""源注册表（sources.yaml）的加载与校验。

只用 dataclass + pyyaml，不引 pydantic。注册表是顶层 YAML 列表，
每个元素是一个源（见 config/sources.example.yaml）。
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from acquisition import default_sources_path

VALID_LANES = ("news", "auth")
VALID_KINDS = ("rss", "stealth", "twitter-cli", "mediacrawler", "firecrawl_crawl")

_INTERVAL_RE = re.compile(r"^(\d+)([smhd])?$")
_INTERVAL_UNITS = {None: 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


class SourceConfigError(RuntimeError):
    """源注册表配置错误（信息可直接展示给用户）。"""


def parse_interval(value) -> int:
    """把 "30s"/"5m"/"1h"/"2h"/"1d" 或整数解析为秒。"""
    if isinstance(value, bool):
        raise SourceConfigError(f"interval 格式非法：{value!r}")
    if isinstance(value, int):
        if value <= 0:
            raise SourceConfigError(f"interval 必须为正数：{value!r}")
        return value
    match = _INTERVAL_RE.match(str(value).strip())
    if not match:
        raise SourceConfigError(
            f"interval 格式非法：{value!r}（支持 30s/5m/1h/1d 或整数秒）"
        )
    amount, unit = int(match.group(1)), match.group(2)
    if amount <= 0:
        raise SourceConfigError(f"interval 必须为正数：{value!r}")
    return amount * _INTERVAL_UNITS[unit]


@dataclass
class Source:
    """源注册表中的一条源。"""

    id: str
    name: str
    lane: str
    kind: str
    url: str = ""
    interval: int = 300  # 最小抓取间隔（秒），节流用，不是调度
    max_items: int = 30
    enabled: bool = True
    platform: str = ""
    crawler_type: str = ""
    creator_id: str = ""
    command: list = field(default_factory=list)
    extra_args: dict = field(default_factory=dict)


def _validate_entry(entry: dict, index: int) -> Source:
    if not isinstance(entry, dict):
        raise SourceConfigError(f"第 {index + 1} 条源：每条源必须是映射（mapping）")
    label = entry.get("id") or f"第 {index + 1} 条"
    for key in ("id", "lane", "kind"):
        if not entry.get(key):
            raise SourceConfigError(f"源 {label}：缺少必填字段 {key!r}")
    lane = entry["lane"]
    if lane not in VALID_LANES:
        raise SourceConfigError(
            f"源 {label}：未知 lane {lane!r}（可选：{'/'.join(VALID_LANES)}）"
        )
    kind = entry["kind"]
    if kind not in VALID_KINDS:
        raise SourceConfigError(
            f"源 {label}：未知 kind {kind!r}（可选：{'/'.join(VALID_KINDS)}）"
        )
    if kind in ("rss", "stealth", "firecrawl_crawl") and not entry.get("url"):
        raise SourceConfigError(f"源 {label}：kind={kind} 的源必须配置 url")

    extra_args = entry.get("extra_args") or {}
    if not isinstance(extra_args, dict):
        raise SourceConfigError(f"源 {label}：extra_args 必须是映射")

    return Source(
        id=str(entry["id"]),
        name=str(entry.get("name") or entry["id"]),
        lane=lane,
        kind=kind,
        url=str(entry.get("url") or ""),
        interval=parse_interval(entry.get("interval", 300)),
        max_items=int(entry.get("max_items", 30)),
        enabled=bool(entry.get("enabled", True)),
        platform=str(entry.get("platform") or ""),
        crawler_type=str(entry.get("crawler_type") or ""),
        creator_id=str(entry.get("creator_id") or ""),
        command=list(entry.get("command") or []),
        extra_args=extra_args,
    )


def load_sources(path: Optional[Path] = None) -> list:
    """加载并校验源注册表，返回 Source 列表。"""
    sources_path = Path(path) if path else default_sources_path()
    if not sources_path.is_file():
        raise SourceConfigError(
            f"源注册表不存在：{sources_path}（可用 --sources 指定路径）"
        )
    try:
        payload = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SourceConfigError(f"源注册表 YAML 解析失败：{sources_path}：{exc}") from exc
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise SourceConfigError(f"源注册表顶层必须是列表：{sources_path}")
    sources = []
    seen_ids = set()
    for index, entry in enumerate(payload):
        source = _validate_entry(entry, index)
        if source.id in seen_ids:
            raise SourceConfigError(f"源 {source.id}：id 重复")
        seen_ids.add(source.id)
        sources.append(source)
    return sources
