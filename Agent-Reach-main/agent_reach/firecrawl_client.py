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
    return str(config.get("firecrawl_enabled", "")).strip().lower() in _TRUE_VALUES


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
            timeout=cls._parse_timeout(config.get("firecrawl_timeout")),
        )

    @staticmethod
    def _parse_timeout(value) -> int:
        """脏值（非数字/空/None）回落默认超时，不让坏配置崩调用方。"""
        try:
            return int(value) if value is not None else DEFAULT_TIMEOUT
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT

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
        try:
            body = resp.json()
        except requests.exceptions.JSONDecodeError as exc:
            # 200 但响应不是 JSON（如反代返回 HTML 错误页）
            raise FirecrawlUnavailableError(
                f"firecrawl 返回非 JSON 响应（{path}）：{exc}"
            ) from exc
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
                raise FirecrawlAPIError(
                    f"firecrawl 任务 {status}（{path}）：{body.get('error', '')}"
                )
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
