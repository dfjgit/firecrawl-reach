# -*- coding: utf-8 -*-
"""存储层：JSONL 追加 + SQLite 去重索引与源状态。

- JSONL：``data/<source_id>.jsonl``，每行一个 Item（只写新条目）。
- SQLite：``state.db``
  - ``seen(item_key PRIMARY KEY, source_id, first_seen_at)`` 增量去重索引
  - ``source_state(source_id PRIMARY KEY, last_fetched_at, baseline_done)``
    节流时间戳与 baseline 标记

baseline 语义：源首跑抓到的条目只建 seen，不算 new（避免首跑洪水）。
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from acquisition import acquisition_dir
from acquisition.models import Item, dedup_key
from agent_reach.utils.paths import make_private_dir

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    item_key TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_state (
    source_id TEXT PRIMARY KEY,
    last_fetched_at TEXT,
    baseline_done INTEGER NOT NULL DEFAULT 0
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    """单个 SQLite 连接 + 内部锁；写操作集中在本类，线程安全。"""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root else acquisition_dir()
        make_private_dir(self.root)
        make_private_dir(self.root / "data")
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.root / "state.db", check_same_thread=False)
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ── 源状态 ─────────────────────────────────────

    def last_fetched_at(self, source_id: str) -> Optional[datetime]:
        """该源上次成功抓取时间（无记录返回 None）。"""
        with self._lock:
            row = self._db.execute(
                "SELECT last_fetched_at FROM source_state WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if not row or not row[0]:
            return None
        return datetime.fromisoformat(row[0])

    def baseline_done(self, source_id: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT baseline_done FROM source_state WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return bool(row and row[0])

    # ── 去重落库 ───────────────────────────────────

    def record_items(self, source_id: str, items: list) -> dict:
        """对一批条目做增量去重并落库。

        返回 ``{"new": [Item], "dup": int, "baseline": int}``。
        首跑（baseline 未完成）：全部条目只建 seen，new 为空。
        """
        now = _now_iso()
        with self._lock:
            first_run = not self._is_baseline_done_locked(source_id)
            new_items: list = []
            dup = 0
            for item in items:
                key = dedup_key(item)
                cursor = self._db.execute(
                    "INSERT OR IGNORE INTO seen (item_key, source_id, first_seen_at)"
                    " VALUES (?, ?, ?)",
                    (key, source_id, now),
                )
                if first_run:
                    continue
                if cursor.rowcount:
                    new_items.append(item)
                else:
                    dup += 1
            self._db.execute(
                "INSERT INTO source_state (source_id, last_fetched_at, baseline_done)"
                " VALUES (?, ?, 1)"
                " ON CONFLICT(source_id) DO UPDATE SET"
                " last_fetched_at = excluded.last_fetched_at, baseline_done = 1",
                (source_id, now),
            )
            self._db.commit()
        if new_items:
            self._append_jsonl(source_id, new_items)
        return {
            "new": new_items,
            "dup": dup,
            "baseline": len(items) if first_run else 0,
        }

    def _is_baseline_done_locked(self, source_id: str) -> bool:
        row = self._db.execute(
            "SELECT baseline_done FROM source_state WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        return bool(row and row[0])

    def _append_jsonl(self, source_id: str, items: list) -> None:
        path = self.root / "data" / f"{source_id}.jsonl"
        with open(path, "a", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")

    # ── 读取（latest 命令） ─────────────────────────

    def read_items(
        self,
        source_id: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> list:
        """从 JSONL 读回条目，可按源与时间（fetched_at）过滤，按时间升序。"""
        data = self.root / "data"
        if source_id:
            paths = [data / f"{source_id}.jsonl"]
        else:
            paths = sorted(data.glob("*.jsonl")) if data.is_dir() else []
        items = []
        for path in paths:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = Item.from_dict(json.loads(line))
                except (ValueError, TypeError):
                    continue
                if since is not None:
                    try:
                        fetched = datetime.fromisoformat(item.fetched_at)
                    except ValueError:
                        continue
                    if fetched < since:
                        continue
                items.append(item)
        items.sort(key=lambda i: i.fetched_at)
        return items
