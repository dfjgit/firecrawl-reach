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
        # 复用 stealth 的模块级常量 _CHALLENGE_MARKERS，避免两份反爬特征表漂移
        haystack = f"{title}\n{text[:2000]}".lower()
        for marker in _CHALLENGE_MARKERS:
            if marker in haystack:
                raise FirecrawlReadError(
                    f"{url} 命中反爬验证特征（{marker}），firecrawl 结果不可用"
                )
