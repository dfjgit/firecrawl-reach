# -*- coding: utf-8 -*-
"""健康检查与 blocked 源管理。

blocked.json 由本模块（M2 起）在识别到登录态问题时写入，格式：
``{"blocked": [{source_id, reason, platform, blocked_at}, ...]}``
读取端兼容历史形态（纯 id 列表、`{id: {...}}`）。
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from acquisition import blocked_path
from agent_reach.utils.paths import atomic_write_private_text


def _target(path: Optional[Path]) -> Path:
    return Path(path) if path else blocked_path()


def _load_raw(path: Path) -> list:
    """读出 blocked 条目并归一化为 dict 列表（兼容旧格式）。"""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        entries = data.get("blocked")
        if isinstance(entries, list):
            raw = entries
        else:
            # 兼容 {source_id: {...}} 形态
            raw = [
                {"source_id": str(key), **(value if isinstance(value, dict) else {})}
                for key, value in data.items()
            ]
    else:
        return []
    entries = []
    for entry in raw:
        if isinstance(entry, dict) and entry.get("source_id"):
            entries.append(dict(entry))
        elif isinstance(entry, str):
            entries.append({"source_id": entry})
    return entries


def blocked_sources(path: Optional[Path] = None) -> set:
    """读取 blocked 源 id 集合；文件缺失或损坏时返回空集合。"""
    return {entry["source_id"] for entry in _load_raw(_target(path))}


def blocked_entries(path: Optional[Path] = None) -> list:
    """读取完整 blocked 条目列表（含 reason/platform/blocked_at），供 CLI 展示。"""
    return _load_raw(_target(path))


def _save(entries: list, path: Path) -> None:
    payload = json.dumps({"blocked": entries}, ensure_ascii=False, indent=2)
    atomic_write_private_text(path, payload)


def block_source(
    source_id: str,
    reason: str,
    platform: str = "",
    path: Optional[Path] = None,
) -> None:
    """把源写入 blocked.json（原子写；同 source_id 覆盖旧条目）。"""
    target = _target(path)
    entries = [e for e in _load_raw(target) if e["source_id"] != source_id]
    entries.append(
        {
            "source_id": source_id,
            "reason": reason,
            "platform": platform,
            "blocked_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _save(entries, target)


def unblock_source(source_id: str, path: Optional[Path] = None) -> bool:
    """把源从 blocked.json 移除；返回是否有条目被移除。"""
    target = _target(path)
    entries = _load_raw(target)
    kept = [e for e in entries if e["source_id"] != source_id]
    if len(kept) == len(entries):
        return False
    _save(kept, target)
    return True
