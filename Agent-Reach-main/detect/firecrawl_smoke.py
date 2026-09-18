# -*- coding: utf-8 -*-
"""firecrawl 集成冒烟：真实打自托管栈，验证 map/scrape/crawl 三端点。

栈不可达时打印 SKIP 并以 0 退出（不阻塞 CI/日常自检）。
用法：python detect/firecrawl_smoke.py [url]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
