# Firecrawl × Agent-Reach 整合实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在项目副本 `E:\Projects\Research\agent-reach-firecrawl-main` 中，把自托管 Firecrawl 接入 Agent-Reach 路由层：单页链路新增 firecrawl 后端（Jina → firecrawl → stealth），新增站点级 CLI（map / crawl-site / batch / search）和 acquisition 整站爬取适配器。

**Architecture:** Firecrawl 自托管栈（docker compose，5 个服务，端口 3002）对 Agent-Reach 只是一个 HTTP 服务。副本内新增薄 REST 客户端 `firecrawl_client.py`（v1 API），单页后端 `backends/firecrawl.py` 遵循 stealth 后端契约（失败抛 RuntimeError 子类，web 渠道 catch 裸 Exception 自动 fall through），站点级能力走 CLI + acquisition 适配器。反爬兜底仍由 stealth_kit 承担。

**Tech Stack:** Python ≥3.10、requests（已有依赖）、pytest、Docker Compose、Firecrawl v1 REST API（自托管免认证）。

**关键约束：**

- 只改副本 `E:\Projects\Research\agent-reach-firecrawl-main`，原项目 `agent-reach-playwright-main` 一行不动。
- 副本不是 git 仓库（spec 明确版本管理不在本期范围）：每个 Task 以"测试全绿"为检查点，不做 git commit。
- 代码风格遵循现有约定：line-length 100（ruff E/F/I）、中文 docstring、`requests` 单次调用 + 显式 timeout + 异常包装模式。
- 所有路径相对于 `E:\Projects\Research\agent-reach-firecrawl-main\Agent-Reach-main`（下文简称 `<root>`），除非特别说明。
- Firecrawl API 路径前缀集中在客户端常量 `_API_PREFIX = "/v1"`（端点形状已由上游 e2e 测试 `firecrawl-main/apps/api/src/__tests__/e2e_v1_withAuth/index.test.ts` 证实；未来切 v2 只改一处）。

---

### Task 0: 环境准备与基线

**Files:**
- 无新增代码；建立副本运行环境

- [ ] **Step 1: 在副本内重建 venv 并安装**

```bash
cd /e/Projects/Research/agent-reach-firecrawl-main/Agent-Reach-main
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev,browser]"
```

注意：必须用副本内的 `.venv/Scripts/python` 执行后续所有 pytest/CLI，避免碰到原版的安装。

- [ ] **Step 2: 跑基线测试，确认副本起点是绿的**

```bash
cd /e/Projects/Research/agent-reach-firecrawl-main/Agent-Reach-main
.venv/Scripts/python -m pytest tests/ -q
```

Expected: 全部通过（或仅有与 Playwright 浏览器相关的 skip）。若有失败，先停下排查——不要在不绿的基线上开工。

- [ ] **Step 3: 确认 Docker 可用**

```bash
docker --version && docker compose version
```

Expected: 两者都输出版本号。若缺失，提示用户安装 Docker Desktop，Task 8/9 之前可先跳过继续其他任务。

- [ ] **Step 4: 确认原版校验和快照已就位**

```bash
wc -l /e/Projects/Research/agent-reach-firecrawl-main/docs/superpowers/original-checksums.sha256
```

Expected: `16938`。这是验收时证明原版零改动的基准。

---

### Task 1: `AGENT_REACH_HOME` 环境变量隔离

副本与原版默认共用 `~/.agent-reach/`（config.yaml、acquisition 数据）。新增环境变量让副本可指向自己的数据目录。

**Files:**
- Modify: `agent_reach/config.py`（`Config.__init__`，L113-123 区域）
- Modify: `acquisition/__init__.py`（`acquisition_dir()`，L11-13）
- Test: `tests/test_agent_reach_home.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_agent_reach_home.py`：

```python
# -*- coding: utf-8 -*-
"""AGENT_REACH_HOME 环境变量：副本/原版用户级状态隔离。"""

from pathlib import Path

from acquisition import acquisition_dir
from agent_reach.config import Config


def test_config_uses_agent_reach_home(monkeypatch, tmp_path):
    override = tmp_path / "ar-home"
    monkeypatch.setenv("AGENT_REACH_HOME", str(override))
    config = Config(read_only=True)
    assert config.config_path == override / "config.yaml"


def test_config_default_home_without_env(monkeypatch):
    monkeypatch.delenv("AGENT_REACH_HOME", raising=False)
    config = Config(read_only=True)
    assert config.config_path == Path.home() / ".agent-reach" / "config.yaml"


def test_acquisition_dir_uses_agent_reach_home(monkeypatch, tmp_path):
    override = tmp_path / "ar-home"
    monkeypatch.setenv("AGENT_REACH_HOME", str(override))
    assert acquisition_dir() == override / "acquisition"


def test_acquisition_dir_default_without_env(monkeypatch):
    monkeypatch.delenv("AGENT_REACH_HOME", raising=False)
    assert acquisition_dir() == Path.home() / ".agent-reach" / "acquisition"
```

- [ ] **Step 2: 运行确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_agent_reach_home.py -q
```

Expected: FAIL（`test_config_uses_agent_reach_home` 与 `test_acquisition_dir_uses_agent_reach_home` 断言不成立）。

- [ ] **Step 3: 实现**

`agent_reach/config.py` 的 `Config.__init__` 中，把：

```python
        self.config_path = Path(config_path) if config_path else self.CONFIG_FILE
```

改为：

```python
        if config_path:
            self.config_path = Path(config_path)
        else:
            # AGENT_REACH_HOME 覆盖默认 ~/.agent-reach（副本/多实例隔离用）
            override = os.environ.get("AGENT_REACH_HOME")
            base = Path(override) if override else self.CONFIG_DIR
            self.config_path = base / "config.yaml"
```

（`os` 已在该文件导入；`Config.get` 里有 `os.environ.get` 的使用。）

`acquisition/__init__.py` 的 `acquisition_dir()` 改为：

```python
def acquisition_dir() -> Path:
    """采集模块的私有数据目录（懒计算，便于测试隔离 HOME）。

    设了 AGENT_REACH_HOME 时指向其下，便于副本与原版数据隔离。
    """
    override = os.environ.get("AGENT_REACH_HOME")
    base = Path(override) if override else Path.home() / ".agent-reach"
    return base / "acquisition"
```

并在文件顶部 `from pathlib import Path` 前加 `import os`。

- [ ] **Step 4: 运行确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_agent_reach_home.py tests/test_config.py -q
```

Expected: PASS（含既有 config 测试不回归）。

---

### Task 2: `firecrawl_client.py` REST 客户端

**Files:**
- Create: `agent_reach/firecrawl_client.py`
- Test: `tests/test_firecrawl_client.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_firecrawl_client.py`：

```python
# -*- coding: utf-8 -*-
"""FirecrawlClient：自托管 Firecrawl 栈的薄 REST 客户端。"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from agent_reach.firecrawl_client import (
    FirecrawlAPIError,
    FirecrawlClient,
    FirecrawlUnavailableError,
)


def _resp(payload, ok=True, status_code=200, text=""):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = payload
    return resp


class TestScrape:
    def test_returns_data(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post:
            post.return_value = _resp({"success": True, "data": {"markdown": "# hi"}})
            data = FirecrawlClient().scrape("https://example.com")
        assert data == {"markdown": "# hi"}
        args, kwargs = post.call_args
        assert args[0] == "http://localhost:3002/v1/scrape"
        assert kwargs["json"] == {"url": "https://example.com", "formats": ["markdown"]}

    def test_api_key_header(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post:
            post.return_value = _resp({"success": True, "data": {}})
            FirecrawlClient(api_key="fc-key").scrape("https://example.com")
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer fc-key"

    def test_http_error_raises_api_error(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post:
            post.return_value = _resp({}, ok=False, status_code=500, text="boom")
            with pytest.raises(FirecrawlAPIError, match="HTTP 500"):
                FirecrawlClient().scrape("https://example.com")

    def test_network_error_raises_unavailable(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post:
            post.side_effect = requests.exceptions.ConnectionError("refused")
            with pytest.raises(FirecrawlUnavailableError):
                FirecrawlClient().scrape("https://example.com")

    def test_success_false_raises_api_error(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post:
            post.return_value = _resp({"success": False, "error": "blocked"})
            with pytest.raises(FirecrawlAPIError, match="success=false"):
                FirecrawlClient().scrape("https://example.com")


class TestMap:
    def test_returns_links(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post:
            post.return_value = _resp({"success": True, "links": ["https://a.com/1"]})
            links = FirecrawlClient().map("https://a.com", limit=10)
        assert links == ["https://a.com/1"]
        assert post.call_args.kwargs["json"] == {"url": "https://a.com", "limit": 10}


class TestCrawl:
    def test_polls_until_completed(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post, \
             patch("agent_reach.firecrawl_client.requests.get") as get:
            post.return_value = _resp({"success": True, "id": "job-1"})
            get.return_value = _resp(
                {"status": "completed", "data": [{"markdown": "p1"}]}
            )
            pages = FirecrawlClient().crawl("https://a.com", limit=5)
        assert pages == [{"markdown": "p1"}]
        assert get.call_args.args[0] == "http://localhost:3002/v1/crawl/job-1"

    def test_failed_job_raises(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post, \
             patch("agent_reach.firecrawl_client.requests.get") as get:
            post.return_value = _resp({"success": True, "id": "job-1"})
            get.return_value = _resp({"status": "failed"})
            with pytest.raises(FirecrawlAPIError, match="failed"):
                FirecrawlClient().crawl("https://a.com")

    def test_timeout_raises_unavailable(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post, \
             patch("agent_reach.firecrawl_client.requests.get") as get, \
             patch("agent_reach.firecrawl_client.time.sleep"):
            post.return_value = _resp({"success": True, "id": "job-1"})
            get.return_value = _resp({"status": "scraping", "data": []})
            with pytest.raises(FirecrawlUnavailableError, match="超时"):
                FirecrawlClient().crawl("https://a.com", max_wait=0, poll_interval=0)


class TestBatchAndSearch:
    def test_batch_scrape(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post, \
             patch("agent_reach.firecrawl_client.requests.get") as get:
            post.return_value = _resp({"success": True, "id": "batch-1"})
            get.return_value = _resp({"status": "completed", "data": [{"markdown": "x"}]})
            pages = FirecrawlClient().batch_scrape(["https://a.com", "https://b.com"])
        assert pages == [{"markdown": "x"}]
        assert post.call_args.kwargs["json"]["urls"] == ["https://a.com", "https://b.com"]

    def test_search(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post:
            post.return_value = _resp({"success": True, "data": [{"url": "u", "title": "t"}]})
            results = FirecrawlClient().search("query", limit=3)
        assert results == [{"url": "u", "title": "t"}]


class TestFromConfig:
    def test_reads_config_keys(self):
        config = {"firecrawl_url": "http://fc:3002/", "firecrawl_api_key": "k"}
        client = FirecrawlClient.from_config(config)
        assert client.base_url == "http://fc:3002"
        assert client.api_key == "k"

    def test_defaults(self):
        client = FirecrawlClient.from_config({})
        assert client.base_url == "http://localhost:3002"
        assert client.api_key is None
```

- [ ] **Step 2: 运行确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_firecrawl_client.py -q
```

Expected: FAIL（`ModuleNotFoundError: agent_reach.firecrawl_client`）。

- [ ] **Step 3: 实现**

新建 `agent_reach/firecrawl_client.py`：

```python
# -*- coding: utf-8 -*-
"""Firecrawl 自托管栈的薄 REST 客户端（v1 API）。

只依赖 requests（已有依赖），不引官方 SDK。端点前缀集中在
``_API_PREFIX``，未来切 v2 只改一处。异常约定沿用项目惯例：
网络/超时包装成 FirecrawlUnavailableError，HTTP/业务错误包装成
FirecrawlAPIError，错误体截断 300 字符。
"""

import time
from typing import Optional

import requests

_API_PREFIX = "/v1"
DEFAULT_BASE_URL = "http://localhost:3002"
DEFAULT_TIMEOUT = 30  # 秒；crawl/batch 的等待由 max_wait 控制
_ERROR_BODY_CHARS = 300
_TRUE_VALUES = ("1", "true", "yes", "on")


class FirecrawlError(RuntimeError):
    """Firecrawl 调用失败的基类。"""


class FirecrawlUnavailableError(FirecrawlError):
    """栈不可达 / 网络错误 / 超时 / 任务等待超时。"""


class FirecrawlAPIError(FirecrawlError):
    """HTTP 错误或业务失败（success=false / 任务 failed）。"""


def firecrawl_enabled(config) -> bool:
    """统一的启用判定（config 存的是字符串）。"""
    return str(config.get("firecrawl_enabled", "")).lower() in _TRUE_VALUES


class FirecrawlClient:
    """自托管 Firecrawl 的 v1 REST 客户端。"""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or None
        self.timeout = timeout

    @classmethod
    def from_config(cls, config) -> "FirecrawlClient":
        return cls(
            base_url=str(config.get("firecrawl_url", DEFAULT_BASE_URL)),
            api_key=config.get("firecrawl_api_key"),
            timeout=int(config.get("firecrawl_timeout", DEFAULT_TIMEOUT)),
        )

    # ── 内部 ──────────────────────────────────────────────

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post(self, path: str, payload: dict) -> dict:
        try:
            resp = requests.post(
                f"{self.base_url}{_API_PREFIX}{path}",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise FirecrawlUnavailableError(f"firecrawl 不可达（{path}）：{exc}") from exc
        return self._parse(resp, path)

    def _get(self, path: str) -> dict:
        try:
            resp = requests.get(
                f"{self.base_url}{_API_PREFIX}{path}",
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise FirecrawlUnavailableError(f"firecrawl 不可达（{path}）：{exc}") from exc
        return self._parse(resp, path)

    @staticmethod
    def _parse(resp, path: str) -> dict:
        if not resp.ok:
            raise FirecrawlAPIError(
                f"firecrawl HTTP {resp.status_code}（{path}）："
                f"{resp.text[:_ERROR_BODY_CHARS]}"
            )
        body = resp.json()
        # crawl/batch 的状态查询响应没有 success 字段，原样返回
        if "success" in body and not body["success"]:
            raise FirecrawlAPIError(
                f"firecrawl 返回 success=false（{path}）："
                f"{str(body)[:_ERROR_BODY_CHARS]}"
            )
        return body

    def _wait_job(self, path: str, max_wait: float, poll_interval: float) -> list:
        deadline = time.monotonic() + max_wait
        while True:
            body = self._get(path)
            status = body.get("status")
            if status == "completed":
                return body.get("data", [])
            if status in ("failed", "cancelled"):
                raise FirecrawlAPIError(f"firecrawl 任务 {status}（{path}）")
            if time.monotonic() >= deadline:
                raise FirecrawlUnavailableError(
                    f"firecrawl 任务超时（{path}，{max_wait}s）"
                )
            time.sleep(poll_interval)

    # ── 端点 ──────────────────────────────────────────────

    def ping(self) -> None:
        """可达性探测：拿到任何 HTTP 响应即视为可达。"""
        try:
            requests.get(self.base_url + "/", timeout=min(self.timeout, 5))
        except requests.RequestException as exc:
            raise FirecrawlUnavailableError(f"firecrawl 栈不可达：{exc}") from exc

    def scrape(self, url: str, formats=("markdown",)) -> dict:
        """单页抓取，返回 data dict（含 markdown/metadata 等）。"""
        return self._post("/scrape", {"url": url, "formats": list(formats)})["data"]

    def map(self, url: str, limit: Optional[int] = None) -> list:
        """枚举站点 URL，返回链接列表。"""
        payload = {"url": url}
        if limit:
            payload["limit"] = int(limit)
        return self._post("/map", payload)["links"]

    def crawl(
        self,
        url: str,
        limit: Optional[int] = None,
        max_wait: float = 600,
        poll_interval: float = 2.0,
    ) -> list:
        """整站爬取：提交任务并轮询到完成，返回页面 data 列表。"""
        payload = {"url": url, "scrapeOptions": {"formats": ["markdown"]}}
        if limit:
            payload["limit"] = int(limit)
        job = self._post("/crawl", payload)
        return self._wait_job(f"/crawl/{job['id']}", max_wait, poll_interval)

    def batch_scrape(
        self,
        urls,
        formats=("markdown",),
        max_wait: float = 600,
        poll_interval: float = 2.0,
    ) -> list:
        """批量抓取 URL 列表，返回页面 data 列表。"""
        job = self._post(
            "/batch/scrape", {"urls": list(urls), "formats": list(formats)}
        )
        return self._wait_job(f"/batch/scrape/{job['id']}", max_wait, poll_interval)

    def search(self, query: str, limit: int = 5) -> list:
        """搜索（自托管需配 SearXNG；未配置会返回 success=false）。"""
        return self._post("/search", {"query": query, "limit": int(limit)})["data"]
```

注意：`_parse` 中 crawl/batch 的轮询响应（`{"status": ..., "data": ...}`）没有 `success` 字段，所以用 `"success" in body` 判定——这保证了 Task 测试里 `{"status": "completed", ...}` 不会误判。

- [ ] **Step 4: 运行确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_firecrawl_client.py -q
```

Expected: 全部 PASS。

---

### Task 3: `backends/firecrawl.py` 单页后端

**Files:**
- Create: `agent_reach/backends/firecrawl.py`
- Modify: `agent_reach/backends/__init__.py`（L17-21 现有 stealth re-export 块旁）
- Test: `tests/test_firecrawl_backend.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_firecrawl_backend.py`：

```python
# -*- coding: utf-8 -*-
"""FirecrawlBackend：web 渠道的 firecrawl 单页后端。"""

from unittest.mock import MagicMock, patch

import pytest

from agent_reach.backends.firecrawl import FirecrawlBackend, FirecrawlReadError
from agent_reach.firecrawl_client import FirecrawlUnavailableError

_ENABLED = {"firecrawl_enabled": "true"}


class TestEnabled:
    def test_read_disabled_raises_unavailable(self):
        with pytest.raises(FirecrawlUnavailableError, match="未启用"):
            FirecrawlBackend({}).read("https://example.com")

    def test_check_disabled_is_off(self):
        status, msg = FirecrawlBackend({}).check()
        assert status == "off"
        assert "未启用" in msg


class TestRead:
    def _client(self, data):
        client = MagicMock()
        client.scrape.return_value = data
        return client

    def test_returns_markdown(self):
        with patch("agent_reach.backends.firecrawl.FirecrawlClient") as cls:
            cls.from_config.return_value = self._client(
                {"markdown": "# 正文", "metadata": {"title": "t"}}
            )
            text = FirecrawlBackend(_ENABLED).read("example.com")
        assert text == "# 正文"
        # 缺 scheme 自动补 https://
        cls.from_config.return_value.scrape.assert_called_with("https://example.com")

    def test_challenge_marker_raises_read_error(self):
        with patch("agent_reach.backends.firecrawl.FirecrawlClient") as cls:
            cls.from_config.return_value = self._client(
                {"markdown": "Please verify you are human", "metadata": {}}
            )
            with pytest.raises(FirecrawlReadError, match="反爬验证特征"):
                FirecrawlBackend(_ENABLED).read("https://example.com")

    def test_empty_markdown_raises_read_error(self):
        with patch("agent_reach.backends.firecrawl.FirecrawlClient") as cls:
            cls.from_config.return_value = self._client({"markdown": "  "})
            with pytest.raises(FirecrawlReadError, match="空内容"):
                FirecrawlBackend(_ENABLED).read("https://example.com")

    def test_client_error_propagates(self):
        with patch("agent_reach.backends.firecrawl.FirecrawlClient") as cls:
            cls.from_config.return_value.scrape.side_effect = (
                FirecrawlUnavailableError("down")
            )
            with pytest.raises(FirecrawlUnavailableError):
                FirecrawlBackend(_ENABLED).read("https://example.com")


class TestCheck:
    def test_probe_ok(self):
        with patch("agent_reach.backends.firecrawl.FirecrawlClient") as cls:
            cls.from_config.return_value.base_url = "http://localhost:3002"
            status, msg = FirecrawlBackend(_ENABLED).check(probe=True)
        assert status == "ok"
        assert "可达" in msg

    def test_probe_error(self):
        with patch("agent_reach.backends.firecrawl.FirecrawlClient") as cls:
            cls.from_config.return_value.ping.side_effect = (
                FirecrawlUnavailableError("refused")
            )
            status, msg = FirecrawlBackend(_ENABLED).check(probe=True)
        assert status == "error"
```

- [ ] **Step 2: 运行确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_firecrawl_backend.py -q
```

Expected: FAIL（`ModuleNotFoundError: agent_reach.backends.firecrawl`）。

- [ ] **Step 3: 实现**

新建 `agent_reach/backends/firecrawl.py`：

```python
# -*- coding: utf-8 -*-
"""firecrawl 后端：把自托管 Firecrawl 栈作为 web 渠道的中间层。

单页链路顺序：Jina Reader → firecrawl → stealth。firecrawl 比 Jina
输出可控（actions/formats），但不带反检测——命中反爬特征的页面抛
FirecrawlReadError，由渠道 fall through 到 stealth 兜底。

未启用（firecrawl_enabled != true）时 read 立刻抛
FirecrawlUnavailableError，渠道零开销跳过。配置键：
``firecrawl_enabled`` / ``firecrawl_url`` / ``firecrawl_api_key`` /
``firecrawl_timeout``。
"""

from typing import Tuple

from agent_reach.backends.stealth import _CHALLENGE_MARKERS
from agent_reach.firecrawl_client import (
    FirecrawlClient,
    FirecrawlError,
    FirecrawlUnavailableError,
    firecrawl_enabled,
)


class FirecrawlReadError(RuntimeError):
    """单次读取失败（空内容 / 命中反爬特征），渠道可回退下一后端。"""


class FirecrawlBackend:
    name = "firecrawl"

    def __init__(self, config=None) -> None:
        # config 可为 None（测试/直接用）——空 dict 的 get 与 Config.get 同签名
        self.config = config if config is not None else {}

    def check(self, probe: bool = False) -> Tuple[str, str]:
        """Doctor 风格探测：(status, message)，status ∈ ok/off/error。"""
        if not firecrawl_enabled(self.config):
            return "off", "firecrawl 未启用（agent-reach configure firecrawl-enabled true）"
        client = FirecrawlClient.from_config(self.config)
        if not probe:
            return "ok", f"firecrawl 已启用（{client.base_url}，未探测）"
        try:
            client.ping()
        except FirecrawlError as exc:
            return "error", f"firecrawl 栈不可达：{exc}"
        return "ok", f"firecrawl 栈可达（{client.base_url}）"

    def read(self, url: str) -> str:
        """经 firecrawl 抓取 url，返回 markdown 正文。"""
        if not firecrawl_enabled(self.config):
            raise FirecrawlUnavailableError("firecrawl 未启用")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        client = FirecrawlClient.from_config(self.config)
        # FirecrawlError 是 RuntimeError 子类，直接上抛让渠道 fall through
        data = client.scrape(url)
        markdown = (data.get("markdown") or "").strip()
        metadata = data.get("metadata") or {}
        title = str(metadata.get("title") or "")
        self._raise_if_challenge(title, markdown, url)
        if not markdown:
            raise FirecrawlReadError(f"firecrawl 返回空内容：{url}")
        return markdown

    @staticmethod
    def _raise_if_challenge(title: str, text: str, url: str) -> None:
        haystack = f"{title}\n{text[:2000]}".lower()
        for marker in _CHALLENGE_MARKERS:
            if marker in haystack:
                raise FirecrawlReadError(
                    f"{url} 命中反爬验证特征（{marker}），firecrawl 结果不可用"
                )
```

在 `agent_reach/backends/__init__.py` 现有 stealth re-export 块（L17-21 区域）后追加：

```python
from agent_reach.backends.firecrawl import (  # noqa: E402
    FirecrawlBackend,
    FirecrawlReadError,
)
```

（保持与现有 stealth 导出相同的代码块风格；若该文件用 `__all__`，同步把两个名字加进去。）

- [ ] **Step 4: 运行确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_firecrawl_backend.py tests/test_stealth_backend.py -q
```

Expected: 全部 PASS（stealth 既有测试不回归）。

---

### Task 4: web 渠道接入 firecrawl 后端

**Files:**
- Modify: `agent_reach/channels/web.py`（L20 backends 列表、L44-60 read()、check()）
- Test: `tests/test_web_firecrawl_chain.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_web_firecrawl_chain.py`：

```python
# -*- coding: utf-8 -*-
"""web 渠道：Jina → firecrawl → stealth 回退链。"""

from unittest.mock import patch

import pytest

from agent_reach.channels.web import WebChannel
from agent_reach.firecrawl_client import FirecrawlUnavailableError


def _read(config=None, url="https://example.com"):
    return WebChannel().read(url, config=config)


class TestFallbackChain:
    def test_jina_ok_short_circuits(self):
        with patch.object(WebChannel, "_read_jina", return_value="jina"):
            assert _read() == "jina"

    def test_jina_fails_firecrawl_serves(self):
        with patch.object(WebChannel, "_read_jina", side_effect=RuntimeError("x")), \
             patch("agent_reach.backends.firecrawl.FirecrawlBackend.read",
                   return_value="fc") as fc_read:
            assert _read() == "fc"
        fc_read.assert_called_once()

    def test_firecrawl_fails_stealth_serves(self):
        with patch.object(WebChannel, "_read_jina", side_effect=RuntimeError("x")), \
             patch("agent_reach.backends.firecrawl.FirecrawlBackend.read",
                   side_effect=FirecrawlUnavailableError("down")), \
             patch("agent_reach.backends.stealth.StealthBackend.read",
                   return_value="stealth"):
            assert _read() == "stealth"

    def test_all_fail_raises_with_backend_names(self):
        with patch.object(WebChannel, "_read_jina", side_effect=RuntimeError("j")), \
             patch("agent_reach.backends.firecrawl.FirecrawlBackend.read",
                   side_effect=FirecrawlUnavailableError("f")), \
             patch("agent_reach.backends.stealth.StealthBackend.read",
                   side_effect=RuntimeError("s")):
            with pytest.raises(RuntimeError) as exc_info:
                _read()
        msg = str(exc_info.value)
        assert "firecrawl" in msg and "stealth" in msg

    def test_disabled_firecrawl_costs_nothing(self):
        # 未配置 firecrawl_enabled 时后端立即抛 Unavailable，不打任何 HTTP
        with patch.object(WebChannel, "_read_jina", side_effect=RuntimeError("x")), \
             patch("agent_reach.firecrawl_client.FirecrawlClient.scrape") as scrape, \
             patch("agent_reach.backends.stealth.StealthBackend.read",
                   return_value="stealth"):
            assert _read(config={}) == "stealth"
        scrape.assert_not_called()
```

- [ ] **Step 2: 运行确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_web_firecrawl_chain.py -q
```

Expected: FAIL（`test_jina_fails_firecrawl_serves` 等——firecrawl 不在回退链里，stealth 被直接调用或报 ModuleNotFound）。

- [ ] **Step 3: 实现**

`agent_reach/channels/web.py` 三处改动：

① L20 后端列表：

```python
    backends = ["Jina Reader", "firecrawl", "stealth"]
```

② read() 循环加 firecrawl 分支（完整替换现有 L44-60 的 read 方法）：

```python
    def read(self, url: str, config=None) -> str:
        errors = []
        for backend in self.ordered_backends(config):
            try:
                if backend == "stealth":
                    from agent_reach.backends.stealth import StealthBackend

                    return StealthBackend(config).read(url)
                if backend == "firecrawl":
                    from agent_reach.backends.firecrawl import FirecrawlBackend

                    return FirecrawlBackend(config).read(url)
                return self._read_jina(url)
            except Exception as exc:  # noqa: BLE001 — try the next backend
                errors.append(f"{backend}: {exc}")
        raise RuntimeError("web 读取全部后端失败：" + "；".join(errors))
```

③ check()：先读现有实现（L26-42 区域），在算出 `candidates` 之后、return 之前追加 firecrawl 状态汇总——当 `"firecrawl" in candidates` 时调用 `FirecrawlBackend(config).check(probe=True)`，把结果拼进 message，firecrawl 为 error 时把渠道 status 从 ok 降级为 warn（firecrawl 是中间层，它挂了渠道仍能工作，不该报 error）：

```python
        if "firecrawl" in candidates:
            from agent_reach.backends.firecrawl import FirecrawlBackend

            fc_status, fc_msg = FirecrawlBackend(config).check(probe=True)
            message = f"{message}；{fc_msg}"
            if fc_status == "error" and status == "ok":
                status = "warn"
```

（现有 check 的返回变量名以文件实际为准做适配；保持"非首个候选不做重探测"的现有风格——firecrawl 的 ping 是 5 秒内的轻探测，可接受。）

- [ ] **Step 4: 运行确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_web_firecrawl_chain.py tests/test_channels.py tests/test_doctor.py -q
```

Expected: 全部 PASS（渠道/doctor 既有测试不回归；若无 test_channels.py/test_doctor.py 这两个文件名，用 `ls tests/ | grep -E "channel|doctor"` 找实际文件名替换）。

---

### Task 5: configure CLI 支持 firecrawl 配置键

**Files:**
- Modify: `agent_reach/cli.py`（L82-86 choices、`_cmd_configure` L1300 后）
- Test: `tests/test_cli.py`（追加；沿用该文件现有 fixture 风格）

- [ ] **Step 1: 写失败测试**

在 `tests/test_cli.py` 末尾追加：

```python
class TestConfigureFirecrawl:
    def _args(self, key, value):
        from types import SimpleNamespace

        return SimpleNamespace(
            key=key, value=[value], from_browser=None, platform=None,
            profile=None, sync_legacy_twitter=False,
        )

    def test_firecrawl_enabled_true(self, capsys):
        from agent_reach.cli import _cmd_configure
        from agent_reach.config import Config

        _cmd_configure(self._args("firecrawl-enabled", "true"))
        assert Config(read_only=True).get("firecrawl_enabled") == "true"

    def test_firecrawl_enabled_rejects_garbage(self):
        import pytest

        from agent_reach.cli import _cmd_configure

        with pytest.raises(SystemExit):
            _cmd_configure(self._args("firecrawl-enabled", "maybe"))

    def test_firecrawl_url(self, capsys):
        from agent_reach.cli import _cmd_configure
        from agent_reach.config import Config

        _cmd_configure(self._args("firecrawl-url", "http://fc:3002"))
        assert Config(read_only=True).get("firecrawl_url") == "http://fc:3002"

    def test_firecrawl_key(self, capsys):
        from agent_reach.cli import _cmd_configure
        from agent_reach.config import Config

        _cmd_configure(self._args("firecrawl-key", "fc-xxx"))
        assert Config(read_only=True).get("firecrawl_api_key") == "fc-xxx"
```

（`tests/conftest.py` 的 autouse `isolated_home` fixture 已把 Config 指向临时目录，测试不会碰真实配置。）

- [ ] **Step 2: 运行确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_cli.py -q -k firecrawl
```

Expected: FAIL（`_cmd_configure` 不认识这些 key，落到未知分支或直接报错）。

- [ ] **Step 3: 实现**

`agent_reach/cli.py` 两处改动：

① L82-86 choices 列表加三项：

```python
    p_conf.add_argument("key", nargs="?", default=None,
                        choices=["proxy", "github-token", "groq-key", "openai-key",
                                 "twitter-cookies", "youtube-cookies",
                                 "xhs-cookies",
                                 "firecrawl-url", "firecrawl-key", "firecrawl-enabled"],
                        help="What to configure (omit if using --from-browser)")
```

② `_cmd_configure` 中，在 `if args.key == "proxy":` 分支之后（或其他简单键分支旁）追加：

```python
    elif args.key == "firecrawl-url":
        config.set("firecrawl_url", value)
        print(f"✅ firecrawl 服务地址已保存：{value}")

    elif args.key == "firecrawl-key":
        # 自托管默认免认证，留空即可；云端或开了认证的栈才需要
        config.set("firecrawl_api_key", value)
        print("✅ firecrawl API key 已保存")

    elif args.key == "firecrawl-enabled":
        if value.lower() not in ("true", "false"):
            print("firecrawl-enabled 只接受 true/false")
            raise SystemExit(1)
        config.set("firecrawl_enabled", value.lower())
        print(f"✅ firecrawl_enabled = {value.lower()}")
        if value.lower() == "true":
            print("  提示：先启动栈——cd Agent-Reach-main/deploy/firecrawl && docker compose up -d")
```

注意：`_cmd_configure` 的 `value` 由 `" ".join(args.value)` 得到（L1295），分支插入位置保持 elif 链不断。

- [ ] **Step 4: 运行确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_cli.py -q
```

Expected: 全部 PASS。

---

### Task 6: 站点级 CLI 子命令（map / crawl-site / batch / search）

**Files:**
- Modify: `agent_reach/cli.py`（subparser 注册区 L130-145 后、分发链 L197-222、文件尾部处理器区）
- Test: `tests/test_firecrawl_cli.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_firecrawl_cli.py`：

```python
# -*- coding: utf-8 -*-
"""firecrawl 站点级 CLI：map / crawl-site / batch / search。"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent_reach import cli
from agent_reach.firecrawl_client import FirecrawlUnavailableError


def _client(**overrides):
    client = MagicMock()
    for name, value in overrides.items():
        getattr(client, name).return_value = value
    return client


class TestDisabled:
    def test_exits_when_not_enabled(self, capsys):
        # cli.py 模块级未 import Config（_cmd_doctor 是函数内 import），
        # 所以 patch 目标必须是 agent_reach.config.Config
        with patch("agent_reach.config.Config.get", return_value=""):
            with pytest.raises(SystemExit):
                cli._cmd_map(SimpleNamespace(url="https://a.com", limit=None))
        assert "未启用" in capsys.readouterr().out


class TestMap:
    def test_prints_links(self, capsys):
        client = _client(map=["https://a.com/1", "https://a.com/2"])
        with patch.object(cli, "_firecrawl_client_or_exit", return_value=client):
            cli._cmd_map(SimpleNamespace(url="https://a.com", limit=10))
        out = capsys.readouterr().out
        assert "https://a.com/1" in out and "https://a.com/2" in out
        client.map.assert_called_once_with("https://a.com", limit=10)


class TestCrawlSite:
    def test_writes_jsonl(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("AGENT_REACH_HOME", str(tmp_path))
        pages = [{"markdown": "# p1", "metadata": {"sourceURL": "https://a.com/1"}}]
        client = _client(crawl=pages)
        with patch.object(cli, "_firecrawl_client_or_exit", return_value=client):
            cli._cmd_crawl_site(
                SimpleNamespace(url="https://a.com", limit=5, max_wait=60)
            )
        out = capsys.readouterr().out
        assert "抓取 1 页" in out
        written = list((tmp_path / "acquisition" / "data" / "firecrawl").glob("*.jsonl"))
        assert len(written) == 1
        assert json.loads(written[0].read_text(encoding="utf-8").strip()) == pages[0]
        client.crawl.assert_called_once_with("https://a.com", limit=5, max_wait=60)


class TestBatch:
    def test_reads_file_and_writes_jsonl(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("AGENT_REACH_HOME", str(tmp_path))
        url_file = tmp_path / "urls.txt"
        url_file.write_text("https://a.com\nhttps://b.com\n", encoding="utf-8")
        client = _client(batch_scrape=[{"markdown": "x"}, {"markdown": "y"}])
        with patch.object(cli, "_firecrawl_client_or_exit", return_value=client):
            cli._cmd_batch(SimpleNamespace(file=str(url_file)))
        out = capsys.readouterr().out
        assert "抓取 2/2 页" in out
        client.batch_scrape.assert_called_once_with(["https://a.com", "https://b.com"])

    def test_empty_file_exits(self, tmp_path):
        url_file = tmp_path / "urls.txt"
        url_file.write_text("", encoding="utf-8")
        with patch.object(cli, "_firecrawl_client_or_exit", return_value=_client()):
            with pytest.raises(SystemExit):
                cli._cmd_batch(SimpleNamespace(file=str(url_file)))


class TestSearch:
    def test_prints_results(self, capsys):
        client = _client(search=[{"title": "T", "url": "https://a.com"}])
        with patch.object(cli, "_firecrawl_client_or_exit", return_value=client):
            cli._cmd_search(SimpleNamespace(query="q", limit=3))
        assert "T" in capsys.readouterr().out
        client.search.assert_called_once_with("q", limit=3)


class TestUnreachable:
    def test_ping_failure_exits_with_hint(self, capsys):
        with patch("agent_reach.config.Config.get", side_effect=lambda k, d=None: {
            "firecrawl_enabled": "true"}.get(k, d)), \
             patch("agent_reach.firecrawl_client.FirecrawlClient.ping",
                   side_effect=FirecrawlUnavailableError("refused")):
            with pytest.raises(SystemExit):
                cli._cmd_map(SimpleNamespace(url="https://a.com", limit=None))
        assert "docker compose up" in capsys.readouterr().out
```

- [ ] **Step 2: 运行确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_firecrawl_cli.py -q
```

Expected: FAIL（`AttributeError: module 'agent_reach.cli' has no attribute '_cmd_map'`）。

- [ ] **Step 3: 实现**

`agent_reach/cli.py` 三处改动：

① subparser 注册（插在 crawl 子命令块 L130-145 之后）：

```python
    # ── firecrawl 站点级命令 ──
    p_map = sub.add_parser("map", help="枚举站点全部 URL（firecrawl）")
    p_map.add_argument("url", help="站点入口 URL")
    p_map.add_argument("--limit", type=int, default=None, help="最多返回 URL 数")

    p_crawl_site = sub.add_parser("crawl-site", help="整站爬取为 markdown（firecrawl）")
    p_crawl_site.add_argument("url", help="站点入口 URL")
    p_crawl_site.add_argument("--limit", type=int, default=100,
                              help="最多抓取页面数（默认 100）")
    p_crawl_site.add_argument("--max-wait", type=int, default=600,
                              help="任务最长等待秒数（默认 600）")

    p_batch = sub.add_parser("batch", help="批量抓取 URL 清单（firecrawl）")
    p_batch.add_argument("file", help="每行一个 URL 的文本文件")

    p_search = sub.add_parser("search", help="搜索并抓取结果（firecrawl，需配 SearXNG）")
    p_search.add_argument("query", help="搜索词")
    p_search.add_argument("--limit", type=int, default=5, help="结果数（默认 5）")
```

② 分发链（L197-222 区域，仿现有 elif 追加）：

```python
    elif args.command == "map":
        _cmd_map(args)
    elif args.command == "crawl-site":
        _cmd_crawl_site(args)
    elif args.command == "batch":
        _cmd_batch(args)
    elif args.command == "search":
        _cmd_search(args)
```

③ 处理器（放在 `_cmd_crawl` 附近，保持 _cmd_* 集中）：

```python
def _firecrawl_client_or_exit():
    """站点级命令公共入口：未启用/不可达时给出可操作提示并退出。"""
    from agent_reach.config import Config
    from agent_reach.firecrawl_client import (
        FirecrawlClient,
        FirecrawlError,
        firecrawl_enabled,
    )

    config = Config(read_only=True)
    if not firecrawl_enabled(config):
        print("[X] firecrawl 未启用。先启动栈并执行："
              "agent-reach configure firecrawl-enabled true")
        raise SystemExit(1)
    client = FirecrawlClient.from_config(config)
    try:
        client.ping()
    except FirecrawlError as exc:
        print(f"[X] firecrawl 栈不可达：{exc}")
        print("    启动：cd Agent-Reach-main/deploy/firecrawl && docker compose up -d")
        raise SystemExit(1)
    return client


def _dump_firecrawl_pages(pages: list, label: str):
    """firecrawl 页面列表写 JSONL 到 acquisition 数据目录，返回路径。"""
    from datetime import datetime, timezone

    from acquisition import data_dir

    out_dir = data_dir() / "firecrawl"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{label}-{ts}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for page in pages:
            fh.write(json.dumps(page, ensure_ascii=False) + "\n")
    return path


def _cmd_map(args):
    from agent_reach.firecrawl_client import FirecrawlError

    client = _firecrawl_client_or_exit()
    try:
        links = client.map(args.url, limit=args.limit)
    except FirecrawlError as exc:
        print(f"[X] map 失败：{exc}")
        raise SystemExit(1)
    for link in links:
        print(link)
    print(f"共 {len(links)} 个 URL", file=sys.stderr)


def _cmd_crawl_site(args):
    from agent_reach.firecrawl_client import FirecrawlError

    client = _firecrawl_client_or_exit()
    print(f"开始整站爬取 {args.url}（limit={args.limit}）…", file=sys.stderr)
    try:
        pages = client.crawl(args.url, limit=args.limit, max_wait=args.max_wait)
    except FirecrawlError as exc:
        print(f"[X] crawl 失败：{exc}")
        raise SystemExit(1)
    path = _dump_firecrawl_pages(pages, "crawl")
    print(f"✅ 抓取 {len(pages)} 页，已写入 {path}")


def _cmd_batch(args):
    from agent_reach.firecrawl_client import FirecrawlError

    client = _firecrawl_client_or_exit()
    with open(args.file, encoding="utf-8") as fh:
        urls = [line.strip() for line in fh if line.strip()]
    if not urls:
        print(f"[X] {args.file} 为空（每行一个 URL）")
        raise SystemExit(1)
    try:
        pages = client.batch_scrape(urls)
    except FirecrawlError as exc:
        print(f"[X] batch 失败：{exc}")
        raise SystemExit(1)
    path = _dump_firecrawl_pages(pages, "batch")
    print(f"✅ 抓取 {len(pages)}/{len(urls)} 页，已写入 {path}")


def _cmd_search(args):
    from agent_reach.firecrawl_client import FirecrawlError

    client = _firecrawl_client_or_exit()
    try:
        results = client.search(args.query, limit=args.limit)
    except FirecrawlError as exc:
        print(f"[X] search 失败：{exc}")
        raise SystemExit(1)
    for item in results:
        print(f"{item.get('title') or ''}\n  {item.get('url') or ''}\n")
```

注意：`cli.py` 顶部已 `import json`/`import sys`（`_cmd_doctor` 用了两者）——实现时确认，缺则补 import。

- [ ] **Step 4: 运行确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_firecrawl_cli.py tests/test_cli.py -q
```

Expected: 全部 PASS。

---

### Task 7: acquisition `firecrawl_crawl` 适配器

**Files:**
- Create: `acquisition/adapters/firecrawl.py`
- Modify: `acquisition/sources.py`（L18 `VALID_KINDS`、L83 url 必填校验）
- Modify: `acquisition/router.py`（L34-40 `ADAPTERS` 注册 + 顶部 import）
- Modify: `config/sources.example.yaml`（加示例源）
- Test: `tests/test_firecrawl_crawl_adapter.py`（新建）

**设计说明**：增量去重复用 router 现有机制（state.db 按 URL/标题判重，与 rss/stealth 源一致），不引入 Firecrawl changeTracking——这是对 spec 第 3 节"changeTracking 做增量"的简化，效果等价且零新机制。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_firecrawl_crawl_adapter.py`：

```python
# -*- coding: utf-8 -*-
"""firecrawl_crawl 适配器：整站爬取源。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from acquisition.adapters.base import AdapterError
from acquisition.adapters.firecrawl import FirecrawlCrawlAdapter
from acquisition.sources import SourceConfigError, load_sources


def _source(**overrides):
    base = dict(
        id="fc1", name="示例站", lane="news", kind="firecrawl_crawl",
        url="https://docs.example.com", interval=300, max_items=10,
        enabled=True, platform="", crawler_type="", creator_id="",
        command=[], extra_args={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


_PAGES = [
    {"markdown": "# 页面一", "metadata": {"sourceURL": "https://docs.example.com/a",
                                          "title": "页面一"}},
    {"markdown": "# 页面二", "metadata": {"sourceURL": "https://docs.example.com/b"}},
    {"markdown": "", "metadata": {"sourceURL": "https://docs.example.com/empty"}},
]


class TestFetch:
    def test_pages_become_items(self):
        client = MagicMock()
        client.crawl.return_value = _PAGES
        items = FirecrawlCrawlAdapter(client=client).fetch(_source())
        assert len(items) == 2  # 空 markdown 页面被跳过
        assert items[0].title == "页面一"
        assert items[0].url == "https://docs.example.com/a"
        assert items[0].content == "# 页面一"
        assert items[0].snapshot is True
        assert items[1].title == "https://docs.example.com/b"  # 无 title 退化为 URL
        client.crawl.assert_called_once_with("https://docs.example.com", limit=10)

    def test_crawl_limit_from_extra_args(self):
        client = MagicMock()
        client.crawl.return_value = _PAGES
        FirecrawlCrawlAdapter(client=client).fetch(
            _source(extra_args={"crawl_limit": 50})
        )
        client.crawl.assert_called_once_with("https://docs.example.com", limit=50)

    def test_client_error_wrapped(self):
        from agent_reach.firecrawl_client import FirecrawlUnavailableError

        client = MagicMock()
        client.crawl.side_effect = FirecrawlUnavailableError("down")
        with pytest.raises(AdapterError, match="整站爬取失败"):
            FirecrawlCrawlAdapter(client=client).fetch(_source())

    def test_empty_result_raises(self):
        client = MagicMock()
        client.crawl.return_value = []
        with pytest.raises(AdapterError, match="为空"):
            FirecrawlCrawlAdapter(client=client).fetch(_source())


class TestSourceValidation:
    def test_kind_accepted(self, tmp_path):
        sources_file = tmp_path / "sources.yaml"
        sources_file.write_text(
            "- id: fc1\n"
            "  lane: news\n"
            "  kind: firecrawl_crawl\n"
            "  url: https://docs.example.com\n",
            encoding="utf-8",
        )
        sources = load_sources(sources_file)
        assert sources[0].kind == "firecrawl_crawl"

    def test_url_required(self, tmp_path):
        sources_file = tmp_path / "sources.yaml"
        sources_file.write_text(
            "- id: fc1\n"
            "  lane: news\n"
            "  kind: firecrawl_crawl\n",
            encoding="utf-8",
        )
        with pytest.raises(SourceConfigError, match="url"):
            load_sources(sources_file)
```

- [ ] **Step 2: 运行确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_firecrawl_crawl_adapter.py -q
```

Expected: FAIL（`ModuleNotFoundError: acquisition.adapters.firecrawl`；validation 测试报"未知 kind"）。

- [ ] **Step 3: 实现**

① 新建 `acquisition/adapters/firecrawl.py`：

```python
# -*- coding: utf-8 -*-
"""firecrawl_crawl 适配器：整站爬取源（firecrawl /crawl）。

入口 URL 整站爬取，每页一条 snapshot Item；空 markdown 页跳过。
增量去重交给 router/state.db（按 URL/标题判重，与 rss/stealth 源一致）。

backend 必须带真实 Config（firecrawl_enabled/firecrawl_url 由它控制）；
未启用时直接 AdapterError，源进 blocked 流程。
"""

from acquisition.adapters.base import Adapter, AdapterError
from acquisition.models import Item
from acquisition.sources import Source
from agent_reach.config import Config
from agent_reach.firecrawl_client import (
    FirecrawlClient,
    FirecrawlError,
    firecrawl_enabled,
)


class FirecrawlCrawlAdapter(Adapter):
    """整站爬取：每页 markdown 作为一条 snapshot Item。"""

    def __init__(self, client=None) -> None:
        # client 可注入（测试 mock）；None 时每次 fetch 由 Config 构造
        self._client = client

    def fetch(self, source: Source) -> list:
        if self._client is not None:
            client = self._client
        else:
            config = Config(read_only=True)
            if not firecrawl_enabled(config):
                raise AdapterError(
                    "firecrawl 未启用（agent-reach configure firecrawl-enabled true）"
                )
            client = FirecrawlClient.from_config(config)

        limit = int(source.extra_args.get("crawl_limit", source.max_items))
        try:
            pages = client.crawl(source.url, limit=limit)
        except FirecrawlError as exc:
            raise AdapterError(f"firecrawl 整站爬取失败 {source.url}：{exc}") from exc

        items = []
        for page in pages[: source.max_items]:
            markdown = (page.get("markdown") or "").strip()
            if not markdown:
                continue
            metadata = page.get("metadata") or {}
            page_url = str(
                metadata.get("sourceURL") or metadata.get("url") or source.url
            )
            items.append(
                Item(
                    source_id=source.id,
                    lane=source.lane,
                    platform=source.platform or "web",
                    author=source.name,
                    title=str(metadata.get("title") or page_url)[:80],
                    content=markdown,
                    url=page_url,
                    published_at="",
                    snapshot=True,
                )
            )
        if not items:
            raise AdapterError(f"firecrawl 整站爬取结果为空：{source.url}")
        return items


def fetch(source: Source) -> list:
    """模块级便捷入口（路由器按 kind 分发用）。"""
    return FirecrawlCrawlAdapter().fetch(source)
```

② `acquisition/sources.py`：

```python
VALID_KINDS = ("rss", "stealth", "twitter-cli", "mediacrawler", "firecrawl_crawl")
```

L83 改为：

```python
    if kind in ("rss", "stealth", "firecrawl_crawl") and not entry.get("url"):
```

③ `acquisition/router.py`：顶部 adapter import 处加 `firecrawl`（仿现有 `from acquisition.adapters import ...` 行的实际写法），`ADAPTERS` dict 加一行：

```python
    "firecrawl_crawl": firecrawl.fetch,
```

并发分组无需改动：新 kind 默认落入 news 并发池（纯 HTTP，无 stealth 的 greenlet 限制）。

④ `config/sources.example.yaml` 末尾追加示例：

```yaml
# 整站爬取（firecrawl 自托管栈；先 agent-reach configure firecrawl-enabled true）
# - id: firecrawl-docs
#   name: 示例文档站
#   lane: news
#   kind: firecrawl_crawl
#   url: https://docs.example.com
#   interval: 6h
#   max_items: 50
#   extra_args:
#     crawl_limit: 200   # 每次整站爬取的页面上限
```

- [ ] **Step 4: 运行确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_firecrawl_crawl_adapter.py tests/test_sources.py tests/test_router.py -q
```

Expected: 全部 PASS（sources/router 既有测试不回归；文件名以 tests/ 下实际为准）。

---

### Task 8: Firecrawl 自托管部署文件

**Files:**
- Create: `deploy/firecrawl/docker-compose.yaml`
- Create: `deploy/firecrawl/README.md`

基于上游 `firecrawl-main/docker-compose.yaml` 精简：用预构建镜像替代本地 build、去掉 FoundationDB 实验服务（默认 NUQ 走 Postgres）、默认免认证。

- [ ] **Step 1: 写 docker-compose.yaml**

新建 `deploy/firecrawl/docker-compose.yaml`：

```yaml
# Firecrawl 自托管栈（精简版）：预构建镜像 + 免认证 + Postgres 队列后端。
# 上游完整版见 firecrawl-main/docker-compose.yaml；本文件差异：
#   - image 替代 build（免本地构建）
#   - 去掉 FoundationDB 实验服务（NUQ 默认走 nuq-postgres）
#   - USE_DB_AUTHENTICATION=false（自托管免 API key）
name: firecrawl

x-common-env: &common-env
  REDIS_URL: redis://redis:6379
  REDIS_RATE_LIMIT_URL: redis://redis:6379
  PLAYWRIGHT_MICROSERVICE_URL: http://playwright-service:3000/scrape
  POSTGRES_USER: postgres
  POSTGRES_PASSWORD: postgres
  POSTGRES_DB: postgres
  POSTGRES_HOST: nuq-postgres
  POSTGRES_PORT: 5432
  USE_DB_AUTHENTICATION: "false"
  NUM_WORKERS_PER_QUEUE: ${NUM_WORKERS_PER_QUEUE:-8}
  # 可选：搜索功能（agent-reach search）需要自建 SearXNG 后填这两项
  SEARXNG_ENDPOINT: ${SEARXNG_ENDPOINT}
  SEARXNG_ENGINES: ${SEARXNG_ENGINES}

services:
  playwright-service:
    image: ghcr.io/firecrawl/playwright-service:latest
    environment:
      PORT: 3000
      MAX_CONCURRENT_PAGES: ${CRAWL_CONCURRENT_REQUESTS:-10}
      BLOCK_MEDIA: "true"
    networks:
      - backend
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    cpus: 2.0
    mem_limit: 4G

  api:
    image: ghcr.io/firecrawl/firecrawl:latest
    ulimits:
      nofile:
        soft: 65535
        hard: 65535
    environment:
      <<: *common-env
      HOST: "0.0.0.0"
      PORT: 3002
      NUQ_RABBITMQ_URL: amqp://rabbitmq:5672
      ENV: local
    depends_on:
      redis:
        condition: service_started
      playwright-service:
        condition: service_started
      rabbitmq:
        condition: service_healthy
      nuq-postgres:
        condition: service_started
    ports:
      - "${PORT:-3002}:3002"
    command: node dist/src/harness.js --start-docker
    networks:
      - backend
    cpus: 4.0
    mem_limit: 8G

  redis:
    image: redis:alpine
    command: redis-server --bind 0.0.0.0
    networks:
      - backend

  rabbitmq:
    image: rabbitmq:3-management
    command: rabbitmq-server
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "-q", "check_running"]
      interval: 5s
      timeout: 5s
      retries: 3
      start_period: 5s
    networks:
      - backend

  nuq-postgres:
    image: ghcr.io/firecrawl/nuq-postgres:latest
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: postgres
    networks:
      - backend

networks:
  backend:
    driver: bridge
```

- [ ] **Step 2: 写 README.md**

新建 `deploy/firecrawl/README.md`：

```markdown
# Firecrawl 自托管栈

Agent-Reach 的 firecrawl 后端 / 站点级命令依赖的本机服务。

## 启动

```bash
cd Agent-Reach-main/deploy/firecrawl
docker compose up -d
```

首次启动需拉取约数 GB 镜像（api / playwright-service / nuq-postgres / redis / rabbitmq）。
就绪后 API 在 http://localhost:3002 （免认证）。

## 接入 Agent-Reach

```bash
agent-reach configure firecrawl-enabled true
# 非默认地址时：agent-reach configure firecrawl-url http://<host>:3002
agent-reach doctor   # web 渠道体检里应看到 firecrawl 栈可达
```

## 常用命令

- 停止：`docker compose down`
- 日志：`docker compose logs -f api`
- 资源：常驻约 2-4GB 内存；不用时建议 down 掉

## 注意

- 自托管版无 Fire-engine stealth / 代理池（云端独占）；反爬由 Agent-Reach 的 stealth 后端兜底。
- `agent-reach search` 需要额外部署 SearXNG 并设置 SEARXNG_ENDPOINT/SEARXNG_ENGINES 环境变量后再 up。
- 镜像 tag 用 latest；要固定版本时把三个 ghcr.io 镜像改成具体 tag。
```

- [ ] **Step 3: 起栈验证（需 Docker）**

```bash
cd /e/Projects/Research/agent-reach-firecrawl-main/Agent-Reach-main/deploy/firecrawl
docker compose up -d
docker compose ps
```

Expected: 5 个服务 running；`curl http://localhost:3002/` 有 HTTP 响应。Docker 不可用则记录跳过，继续 Task 9 时标注。

---

### Task 9: 集成冒烟脚本 `detect/firecrawl_smoke.py`

仿 `detect/` 目录现有自检工具风格；栈不可达时明确 skip（退出码 0 并打印 SKIP），可达时实跑 map + scrape + crawl。

**Files:**
- Create: `detect/firecrawl_smoke.py`

- [ ] **Step 1: 实现**

```python
# -*- coding: utf-8 -*-
"""firecrawl 集成冒烟：真实打自托管栈，验证 map/scrape/crawl 三端点。

栈不可达时打印 SKIP 并以 0 退出（不阻塞 CI/日常自检）。
用法：python detect/firecrawl_smoke.py [url]
"""

import sys

from agent_reach.config import Config
from agent_reach.firecrawl_client import (
    FirecrawlClient,
    FirecrawlError,
    firecrawl_enabled,
)

_TARGET = "https://example.com"


def main() -> int:
    config = Config(read_only=True)
    if not firecrawl_enabled(config):
        print("SKIP: firecrawl 未启用（agent-reach configure firecrawl-enabled true）")
        return 0
    client = FirecrawlClient.from_config(config)
    try:
        client.ping()
    except FirecrawlError as exc:
        print(f"SKIP: firecrawl 栈不可达（{exc}）")
        return 0

    url = sys.argv[1] if len(sys.argv) > 1 else _TARGET
    failures = 0

    try:
        data = client.scrape(url)
        ok = bool((data.get("markdown") or "").strip())
        print(f"{'PASS' if ok else 'FAIL'}: scrape {url} -> "
              f"{len(data.get('markdown') or '')} chars markdown")
        failures += 0 if ok else 1
    except FirecrawlError as exc:
        print(f"FAIL: scrape {url} -> {exc}")
        failures += 1

    try:
        links = client.map(url, limit=5)
        print(f"PASS: map {url} -> {len(links)} links")
    except FirecrawlError as exc:
        print(f"FAIL: map {url} -> {exc}")
        failures += 1

    try:
        pages = client.crawl(url, limit=2, max_wait=120)
        ok = len(pages) > 0
        print(f"{'PASS' if ok else 'FAIL'}: crawl {url} -> {len(pages)} pages")
        failures += 0 if ok else 1
    except FirecrawlError as exc:
        print(f"FAIL: crawl {url} -> {exc}")
        failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 运行**

```bash
cd /e/Projects/Research/agent-reach-firecrawl-main/Agent-Reach-main
.venv/Scripts/agent-reach.exe configure firecrawl-enabled true
.venv/Scripts/python detect/firecrawl_smoke.py
```

（`agent-reach.exe` 是 `pip install -e` 生成的 entry point，指向副本 venv；configure 写入默认 `~/.agent-reach/config.yaml`，冒烟脚本读同一份配置。）

Expected: 栈在跑 → 三个 PASS；栈没跑 → SKIP，退出码 0。

---

### Task 10: 更新 Agent 路由指南 SKILL.md / SKILL_en.md

**Files:**
- Modify: `agent_reach/skill/SKILL.md`（"零配置快速命令 → 通用网页阅读"小节）
- Modify: `agent_reach/skill/SKILL_en.md`（对应英文小节）

- [ ] **Step 1: SKILL.md 修改**

在"通用网页阅读"小节的 Jina/curl 说明后追加：

```markdown
### 站点级抓取（firecrawl，需先启用）

doctor 的 web 渠道显示"firecrawl 栈可达"时可用；未启用则跳过本小节：

- 枚举站点全部 URL：`agent-reach map <站点URL> [--limit N]`
- 整站爬取为 markdown：`agent-reach crawl-site <站点URL> [--limit N]`（结果写入 acquisition 数据目录的 JSONL）
- 批量抓取 URL 清单：`agent-reach batch <每行一个URL的文件>`
- 搜索：`agent-reach search <关键词> [--limit N]`（需栈配 SearXNG）

单页阅读的优先级不变：Jina Reader → firecrawl → stealth。firecrawl 命中
反爬验证会自动回退 stealth，无需人工干预。
定期整站源：sources.yaml 用 `kind: firecrawl_crawl` + 入口 url（见
config/sources.example.yaml）。
```

- [ ] **Step 2: SKILL_en.md 同步**

对应英文翻译版，保持小节结构一致。

- [ ] **Step 3: 校验**

```bash
.venv/Scripts/python -m pytest tests/ -q -k skill
```

Expected: PASS（若有 SKILL.md 内容断言的测试，同步更新断言）。

---

### Task 11: 验收

- [ ] **Step 1: 副本全量测试**

```bash
cd /e/Projects/Research/agent-reach-firecrawl-main/Agent-Reach-main
.venv/Scripts/python -m pytest tests/ -q
```

Expected: 全部通过（浏览器相关用例按基线 skip）。

- [ ] **Step 2: doctor 体检**

```bash
.venv/Scripts/agent-reach.exe doctor
```

Expected: web 渠道 ok（firecrawl 启用且栈在跑时，message 含"firecrawl 栈可达"；栈没跑时为 warn 而非 error）。

- [ ] **Step 3: ruff 检查**

```bash
.venv/Scripts/python -m ruff check agent_reach acquisition detect
```

Expected: 无新增告警。

- [ ] **Step 4: 原版零改动复验**

```bash
cd /e/Projects/Research/agent-reach-playwright-main
sha256sum -c /e/Projects/Research/agent-reach-firecrawl-main/docs/superpowers/original-checksums.sha256 2>&1 | grep -v ': OK$' | head
```

Expected: 无输出（16938 个文件全部一致），证明原项目未被触碰。

- [ ] **Step 5: 端到端（可选，需 Docker 栈在跑）**

```bash
.venv/Scripts/agent-reach.exe configure firecrawl-enabled true
.venv/Scripts/agent-reach.exe map https://example.com --limit 5
.venv/Scripts/agent-reach.exe crawl-site https://example.com --limit 3
```

Expected: map 打印 URL 列表；crawl-site 落盘 JSONL 并打印路径。
