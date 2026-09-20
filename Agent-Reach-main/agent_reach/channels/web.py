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
# Jina 遇 Cloudflare 等挑战页时会返回 HTTP 200 + 挑战页正文（不是目标内容）。
# 必须识别并抛错，才能让 read() 回退到下一个后端，而不是把垃圾当正文入库。
# 源自上游 commit b62efd0（fix(web): harden reader input and response boundaries）。
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_ANTIBOT_SCAN_BYTES = 4096


def _is_antibot_page(body: bytes) -> bool:
    """识别高置信度的 Jina / Cloudflare 挑战页。

    保守设计：单一特征不判定，需「挑战页标题」+「挑战脚本/痕迹」同时成立，
    避免把正文里偶然出现的字样误判成挑战页。
    上游 b62efd0 的 cloudflare 分支只认 "Attention Required! | Cloudflare" 标题，
    会漏掉更常见的 "Just a moment..."（Cloudflare JS 挑战页）→ 本地补强覆盖两者。
    """
    sample = body[:_ANTIBOT_SCAN_BYTES].decode("utf-8", errors="ignore").casefold()

    jina_captcha_warning = "warning:" in sample and "requiring captcha" in sample
    challenge_structure = any(
        marker in sample
        for marker in (
            "title: just a moment...",
            "## performing security verification",
            "title: attention required! | cloudflare",
        )
    )
    challenge_title = any(
        marker in sample
        for marker in (
            "title: just a moment...",
            "title: attention required! | cloudflare",
        )
    )
    cloudflare_block = challenge_title and (
        "ray id" in sample or "/cdn-cgi/challenge-platform/" in sample
    )
    return (jina_captcha_warning and challenge_structure) or cloudflare_block


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
        """通过 Jina Reader 读取网页，返回 Markdown 全文。

        命中反爬挑战页或超出响应上限时抛错，交由 read() 回退到下一个后端。
        """
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        jina_url = f"https://r.jina.ai/{url}"
        req = urllib.request.Request(
            jina_url,
            headers={"User-Agent": _UA, "Accept": "text/plain"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) > _MAX_RESPONSE_BYTES:
            raise ValueError(
                f"Jina Reader 响应超过 {_MAX_RESPONSE_BYTES} 字节上限，已中止"
            )
        if _is_antibot_page(body):
            raise RuntimeError(
                "Jina Reader 返回了反爬验证页（Cloudflare 挑战 / 需验证码），"
                "未取到目标内容；交由下一后端重试"
            )
        return body.decode("utf-8")
