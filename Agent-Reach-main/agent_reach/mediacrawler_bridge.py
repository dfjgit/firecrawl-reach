# -*- coding: utf-8 -*-
"""MediaCrawler bridge — locate and invoke the vendored MediaCrawler subtree.

The vendored project lives in ``<repo>/mediacrawler/`` (self-contained,
NON-COMMERCIAL LEARNING LICENSE — see ``mediacrawler/LICENSE``).  Following
the Agent Reach philosophy ("agents call upstream tools directly"), it is
executed as a subprocess with its own ``.venv`` interpreter, never imported
into the agent_reach process.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

MEDIACRAWLER_DIR = Path(__file__).resolve().parent.parent / "mediacrawler"

# agent-reach 渠道名 → MediaCrawler --platform 值
PLATFORMS = {
    "xhs": "xhs",
    "douyin": "dy",
    "kuaishou": "ks",
    "bilibili": "bili",
    "weibo": "wb",
    "tieba": "tieba",
    "zhihu": "zhihu",
}

INSTALL_HINT = (
    "MediaCrawler 依赖未安装。安装方式：\n"
    "  cd mediacrawler\n"
    "  uv venv .venv\n"
    "  uv pip install -r requirements.txt\n"
    "（或：python -m venv .venv && .venv/Scripts/pip install -r requirements.txt）"
)


def mediacrawler_available() -> bool:
    """True when the vendored subtree exists."""
    return (MEDIACRAWLER_DIR / "main.py").is_file()


def _venv_python() -> Optional[Path]:
    if os.name == "nt":
        candidate = MEDIACRAWLER_DIR / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = MEDIACRAWLER_DIR / ".venv" / "bin" / "python"
    return candidate if candidate.is_file() else None


def mediacrawler_python() -> str:
    """Interpreter used to run MediaCrawler: subtree venv preferred."""
    venv = _venv_python()
    return str(venv) if venv else sys.executable


def deps_ready() -> bool:
    """Cheap readiness probe: venv exists and has the key packages installed."""
    venv = _venv_python()
    if venv is None:
        return False
    site_packages = venv.parent.parent / "Lib" / "site-packages"
    if os.name != "nt":
        # POSIX venv layout: .venv/lib/python3.x/site-packages
        lib_dir = venv.parent.parent / "lib"
        candidates = list(lib_dir.glob("python3.*/site-packages")) if lib_dir.is_dir() else []
        site_packages = candidates[0] if candidates else site_packages
    if not site_packages.is_dir():
        return False
    return all(
        (site_packages / pkg).exists()
        for pkg in ("playwright", "typer", "httpx")
    )


def login_state_dir(mc_platform: str) -> Path:
    """MediaCrawler caches the per-platform browser profile here (cwd-based)."""
    return MEDIACRAWLER_DIR / "browser_data" / f"{mc_platform}_user_data_dir"


def has_login_state(mc_platform: str) -> bool:
    d = login_state_dir(mc_platform)
    try:
        return d.is_dir() and any(d.iterdir())
    except OSError:
        return False


def build_command(mc_platform: str, extra_args: Optional[List[str]] = None) -> List[str]:
    """Subprocess command for `main.py --platform <p> [extra args...]`."""
    return [
        mediacrawler_python(),
        "main.py",
        "--platform",
        mc_platform,
        *(extra_args or []),
    ]


def run_crawl(mc_platform: str, extra_args: Optional[List[str]] = None) -> int:
    """Run a MediaCrawler crawl in-process-streaming mode. Returns exit code."""
    cmd = build_command(mc_platform, extra_args)
    return subprocess.call(cmd, cwd=str(MEDIACRAWLER_DIR))
