# -*- coding: utf-8 -*-
"""统一数据模型与增量去重键。

Item 字段与设计文档 §7 一致；去重键规则：
有 item_id 时 ``platform:item_id``，否则 ``sha1:sha1(归一化全文)``。
"""

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Item:
    """一条采集到的条目（JSONL 每行一个）。"""

    source_id: str
    lane: str
    platform: str = ""
    item_id: str = ""
    author: str = ""
    title: Optional[str] = None
    content: str = ""
    url: str = ""
    published_at: str = ""
    fetched_at: str = field(default_factory=_now_iso)
    metrics: dict = field(default_factory=dict)
    snapshot: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Item":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def dedup_key(item: Item) -> str:
    """计算去重键：platform+item_id，无 item_id 时退化为内容哈希。

    内容哈希用**归一化全文**（连续空白折叠为单空格，截断 2000 字符）：
    只哈希前 200 字符会在列表页条目共享相同前缀（日期头/栏目名）时
    把不同条目误判为重复（财联社电报 11→2 误杀）；归一化则避免换行/
    缩进抖动导致同一条目每轮都被当成新条目。
    """
    if item.item_id:
        return f"{item.platform}:{item.item_id}"
    normalized = " ".join(item.content.split())[:2000]
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    return f"sha1:{digest}"
