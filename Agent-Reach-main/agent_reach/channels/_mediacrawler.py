# -*- coding: utf-8 -*-
"""MediaCrawler-backed channels — 抖音/快手/微博/贴吧/知乎.

These platforms have no usable CLI upstream; they are served by the vendored
MediaCrawler subtree (``mediacrawler/``), which keeps a logged-in Playwright
browser profile per platform.  Doctor only checks integration + dependency +
login-state presence — it never launches the browser (login is interactive:
first crawl opens a QR-code page).
"""

from agent_reach import mediacrawler_bridge as bridge
from agent_reach.utils.url import host_matches

from .base import Channel


class MediaCrawlerChannel(Channel):
    """Shared check logic for platforms served by the MediaCrawler subtree."""

    backends = ["MediaCrawler"]
    tier = 2

    #: MediaCrawler --platform value (dy/ks/wb/tieba/zhihu)
    mc_platform: str = ""
    #: URL domains this channel handles
    domains: tuple = ()

    def can_handle(self, url: str) -> bool:
        return host_matches(url, *self.domains)

    def check(self, config=None):
        self.active_backend = None

        if not bridge.mediacrawler_available():
            return "off", "MediaCrawler 未集成（mediacrawler/ 目录缺失）"

        if not bridge.deps_ready():
            return "warn", bridge.INSTALL_HINT

        self.active_backend = "MediaCrawler"
        if bridge.has_login_state(self.mc_platform):
            return "ok", "MediaCrawler 就绪，登录态已缓存"
        return "warn", (
            f"MediaCrawler 就绪但 {self.description} 未登录；"
            "首次抓取会打开浏览器扫码登录：\n"
            f"  agent-reach crawl {self.mc_platform} --lt qrcode --type search --keywords 测试"
        )


class DouyinChannel(MediaCrawlerChannel):
    name = "douyin"
    description = "抖音"
    mc_platform = "dy"
    domains = ("douyin.com",)


class KuaishouChannel(MediaCrawlerChannel):
    name = "kuaishou"
    description = "快手"
    mc_platform = "ks"
    domains = ("kuaishou.com",)


class WeiboChannel(MediaCrawlerChannel):
    name = "weibo"
    description = "微博"
    mc_platform = "wb"
    domains = ("weibo.com", "weibo.cn")


class TiebaChannel(MediaCrawlerChannel):
    name = "tieba"
    description = "百度贴吧"
    mc_platform = "tieba"
    domains = ("tieba.baidu.com",)


class ZhihuChannel(MediaCrawlerChannel):
    name = "zhihu"
    description = "知乎"
    mc_platform = "zhihu"
    domains = ("zhihu.com",)
