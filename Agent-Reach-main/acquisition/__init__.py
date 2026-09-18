# -*- coding: utf-8 -*-
"""采集模块（acquisition）：站在 agent-reach 能力层之上的业务层。

无状态 CLI：被调用一次跑一轮，自身不含调度。数据统一放在
``~/.agent-reach/acquisition/`` 私有目录下（owner-only 权限）。
"""

import os
from pathlib import Path


def acquisition_dir() -> Path:
    """采集模块的私有数据目录（懒计算，便于测试隔离 HOME）。

    设了 AGENT_REACH_HOME 时指向其下，便于副本与原版数据隔离。
    """
    override = os.environ.get("AGENT_REACH_HOME")
    base = Path(override) if override else Path.home() / ".agent-reach"
    return base / "acquisition"


def default_sources_path() -> Path:
    """源注册表默认路径。"""
    return acquisition_dir() / "sources.yaml"


def data_dir() -> Path:
    """JSONL 条目目录。"""
    return acquisition_dir() / "data"


def state_db_path() -> Path:
    """SQLite 状态库路径。"""
    return acquisition_dir() / "state.db"


def blocked_path() -> Path:
    """blocked 源清单路径。"""
    return acquisition_dir() / "blocked.json"


def runs_log_path() -> Path:
    """每轮汇总日志路径。"""
    return acquisition_dir() / "runs.log"
