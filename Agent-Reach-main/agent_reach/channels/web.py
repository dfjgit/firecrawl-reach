# -*- coding: utf-8 -*-
"""Web — any URL via Jina Reader, with firecrawl and stealth-browser fallbacks.

Backend order: "Jina Reader" (zero-config, remote rendering) first, then
"firecrawl" (self-hosted Firecrawl stack, when enabled via
`agent-reach configure firecrawl-enabled true`), then "stealth" (local
stealth browser via agent_reach.backends.stealth) for pages that need real
rendering or block plain HTTP. Force a backend with
`agent-reach configure web_backend <name>`.
"""

import urllib.request

from .base import Channel

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


class WebChannel(Channel):
    name = "web"
    description = "任意网页"
    backends = ["Jina Reader", "firecrawl", "stealth"]
    tier = 0

    def can_handle(self, url: str) -> bool:
        return True  # Fallback — handles any URL

    def check(self, config=None):
        # Jina is the zero-overhead primary: no probing needed for it.
        # When the user forces web_backend=stealth, probe the browser path
        # properly (launch + close) so doctor tells the truth about it.
        candidates = self.ordered_backends(config)
        if candidates[0] == "stealth":
            from agent_reach.backends.stealth import StealthBackend

            status, message = StealthBackend(config).check(launch_probe=True)
            self.active_backend = "stealth" if status == "ok" else None
            return status, message
        # 恒可用兜底渠道：无本地命令、不做网络探测（doctor 已有多个渠道触网），保持零开销
        self.active_backend = self.backends[0]
        status, message = "ok", (
            "通过 Jina Reader 读取任意网页（curl https://r.jina.ai/URL）；"
            "渲染/反爬页面回退 stealth 浏览器（web_backend=stealth 可强制）"
        )
        # firecrawl 是中间层：挂载时汇总其状态；它挂了渠道仍能工作，只降级为 warn
        if "firecrawl" in candidates:
            from agent_reach.backends.firecrawl import FirecrawlBackend

            fc_status, fc_msg = FirecrawlBackend(config).check(probe=True)
            message = f"{message}；{fc_msg}"
            if fc_status == "error" and status == "ok":
                status = "warn"
        return status, message

    def read(self, url: str, config=None) -> str:
        """Read a web page as text. Jina first, then firecrawl, stealth as fallback.

        Backend order follows `web_backend` (see Channel.ordered_backends);
        a failing backend falls through to the next one.
        """
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

    def _read_jina(self, url: str) -> str:
        """通过 Jina Reader 读取网页，返回 Markdown 全文。"""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        jina_url = f"https://r.jina.ai/{url}"
        req = urllib.request.Request(
            jina_url,
            headers={"User-Agent": _UA, "Accept": "text/plain"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
