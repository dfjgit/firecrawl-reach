# -*- coding: utf-8 -*-
"""路由器：遍历启用源，节流 → 分发适配器 → 去重落库 → 汇总。

- interval 节流：距 last_fetched_at 不足 interval 的源本轮 skipped（--force 覆盖）。
- kind 无适配器的源本轮记 error，不崩溃。
- blocked.json 中列出的源跳过并记 blocked。
- news lane 的抓取用 ThreadPoolExecutor 并发（max_workers=4），
  落库统一回主线程做（Store 单连接）。
- stealth kind 例外：适配器基于 playwright sync API（greenlet 绑定线程），
  多 stealth 源并发会 "Cannot switch to a different thread"，故串行执行。
- auth lane（登录态源，基本姿态：单账号、低频、不并发）：串行执行，
  源间加随机抖动；抓取前做渠道体检（进程内缓存，一轮内同渠道只查一次），
  非 ok 或抓取中识别到登录态问题 → 写 blocked.json 并记 blocked。
"""

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from acquisition import health
from acquisition.adapters import (
    firecrawl,
    mediacrawler,
    rss,
    stealth,
    twitter_cli,
)
from acquisition.adapters.base import (
    AdapterError,
    LoginInvalidError,
    LoginRequiredError,
)
from acquisition.sources import Source
from acquisition.store import Store
from agent_reach.channels import get_channel
from agent_reach.config import Config

# kind → 适配器 fetch 函数；新引擎 = 新增一个适配器文件 + 这里注册一行
ADAPTERS = {
    "rss": rss.fetch,
    "stealth": stealth.fetch,
    "mediacrawler": mediacrawler.fetch,
    "twitter-cli": twitter_cli.fetch,
    "firecrawl_crawl": firecrawl.fetch,
}

# mc 平台码 → agent-reach 渠道名（渠道体检用）
_MC_CHANNELS = {
    "wb": "weibo",
    "dy": "douyin",
    "ks": "kuaishou",
    "tieba": "tieba",
    "zhihu": "zhihu",
    "xhs": "xiaohongshu",
    "bili": "bilibili",
}

_MAX_WORKERS = 4
_JITTER_RANGE = (1.0, 3.0)  # auth 源间随机抖动（秒），extra_args.jitter=False 关闭


def _summary(source: Source, status: str, **kwargs) -> dict:
    entry = {"source_id": source.id, "status": status, "new": 0, "dup": 0, "error": ""}
    entry.update(kwargs)
    return entry


def _channel_check(channel_name: str) -> tuple:
    """调 agent-reach 渠道体检，返回 (status, message)。"""
    channel = get_channel(channel_name)
    if channel is None:
        return "error", f"渠道不存在：{channel_name}"
    return channel.check(Config(read_only=True))


def _auth_channel_status(source: Source, cache: dict) -> tuple:
    """auth 源抓取前的渠道体检；结果在 cache 里按渠道缓存（一轮只查一次）。"""
    channel_name = _MC_CHANNELS.get(source.platform) if source.kind == "mediacrawler" else None
    if channel_name is None:
        return "ok", ""
    if channel_name not in cache:
        cache[channel_name] = _channel_check(channel_name)
    return cache[channel_name]


def _run_auth_source(source: Source, store: Store, check_cache: dict) -> dict:
    """串行跑一个登录态源：体检 → 抖动 → 抓取 → 落库（由调用方做）。"""
    status, message = _auth_channel_status(source, check_cache)
    if status != "ok":
        health.block_source(source.id, message, platform=source.platform)
        return _summary(source, "blocked", error=f"渠道体检非 ok：{message}")
    if source.extra_args.get("jitter", True):
        time.sleep(random.uniform(*_JITTER_RANGE))
    try:
        items = ADAPTERS[source.kind](source)
    except (LoginRequiredError, LoginInvalidError) as exc:
        health.block_source(source.id, str(exc), platform=source.platform)
        return _summary(source, "blocked", error=str(exc))
    except AdapterError as exc:
        return _summary(source, "error", error=str(exc))
    result = store.record_items(source.id, items)
    return _summary(
        source,
        "ok",
        new=len(result["new"]),
        dup=result["dup"],
        baseline=result["baseline"],
    )


def run_round(
    sources: list,
    store: Store,
    *,
    force: bool = False,
    blocked: Optional[set] = None,
    now: Optional[datetime] = None,
) -> list:
    """跑一轮采集，按源顺序返回每源汇总 dict 列表。"""
    now = now or datetime.now(timezone.utc)
    blocked_ids = blocked if blocked is not None else health.blocked_sources()

    summaries: dict = {}
    news_fetchable: list = []
    news_serial: list = []   # stealth 源：greenlet 绑定线程，串行执行
    auth_fetchable: list = []
    for source in sources:
        if not source.enabled:
            summaries[source.id] = _summary(source, "skipped", error="源已禁用")
            continue
        if source.id in blocked_ids:
            summaries[source.id] = _summary(source, "blocked", error="登录态失效，已 blocked")
            continue
        last = store.last_fetched_at(source.id)
        if not force and last is not None:
            elapsed = (now - last).total_seconds()
            if elapsed < source.interval:
                summaries[source.id] = _summary(
                    source,
                    "skipped",
                    error=f"距上次抓取 {int(elapsed)}s < interval {source.interval}s（节流）",
                )
                continue
        if source.kind not in ADAPTERS:
            summaries[source.id] = _summary(source, "error", error=f"未知 kind：{source.kind}")
            continue
        if source.lane == "auth":
            auth_fetchable.append(source)
        elif source.kind == "stealth":
            news_serial.append(source)
        else:
            news_fetchable.append(source)

    # news lane 各源相互独立，抓取阶段可并发；失败只记 error，不影响其他源
    if news_fetchable:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {pool.submit(ADAPTERS[s.kind], s): s for s in news_fetchable}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    items = future.result()
                except Exception as exc:
                    summaries[source.id] = _summary(source, "error", error=str(exc))
                    continue
                result = store.record_items(source.id, items)
                summaries[source.id] = _summary(
                    source,
                    "ok",
                    new=len(result["new"]),
                    dup=result["dup"],
                    baseline=result["baseline"],
                )

    # stealth 源串行（playwright greenlet 不支持跨线程），单个失败不影响其他源
    for source in news_serial:
        try:
            items = ADAPTERS[source.kind](source)
        except Exception as exc:
            summaries[source.id] = _summary(source, "error", error=str(exc))
            continue
        result = store.record_items(source.id, items)
        summaries[source.id] = _summary(
            source,
            "ok",
            new=len(result["new"]),
            dup=result["dup"],
            baseline=result["baseline"],
        )

    # auth lane 串行 + 抖动（避免触发风控），登录态问题写 blocked
    check_cache: dict = {}
    for source in auth_fetchable:
        try:
            summaries[source.id] = _run_auth_source(source, store, check_cache)
        except Exception as exc:  # 兜底：单个 auth 源异常不影响其他源
            summaries[source.id] = _summary(source, "error", error=str(exc))

    return [summaries[source.id] for source in sources]


def format_summary(entry: dict) -> str:
    """把单源汇总格式化成一行，供 CLI 打印与 runs.log。"""
    parts = [f"{entry['source_id']} → {entry['status']}"]
    if entry["status"] == "ok":
        parts.append(f"new {entry['new']} / dup {entry['dup']}")
        if entry.get("baseline"):
            parts.append(f"（baseline {entry['baseline']} 条只建档不算新）")
    if entry.get("error"):
        parts.append(f"- {entry['error']}")
    return " ".join(parts)
