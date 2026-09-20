# -*- coding: utf-8 -*-
"""Agent Reach — Give your AI Agent eyes to see the entire internet."""

import os
from pathlib import Path

# 本仓库是 firecrawl 版，与 playwright 版是两个独立项目，运行数据必须分开：
# 默认指向**本仓库自己的** .agent-reach，不再落回用户级共享目录 ~/.agent-reach。
# 用 setdefault，显式设置的 AGENT_REACH_HOME 仍然优先（不覆盖已有值）。
# 这样无论怎么启动（CLI / acq-fc.ps1 / 直接 python -m），都不会与 pw 版共用数据。
os.environ.setdefault(
    "AGENT_REACH_HOME",
    str(Path(__file__).resolve().parents[2] / ".agent-reach"),
)

__version__ = "1.5.0"
__author__ = "Neo Reid"

from agent_reach.core import AgentReach

__all__ = ["AgentReach"]
