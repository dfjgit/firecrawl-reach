# -*- coding: utf-8 -*-
"""MediaCrawler 适配器（通道二）：子进程调用 vendored 的 mediacrawler/ 子树。

流程：前置检查（依赖/登录态）→ 构造命令 → subprocess 执行 → 读本次新增
的 jsonl 产物 → 按平台字段映射转 Item。产物布局（读 async_file_writer.py
确认）：``mediacrawler/data/<writer目录>/jsonl/<crawler_type>_contents_<YYYY-MM-DD>.jsonl``，
同一天多次运行追加同一文件，所以用运行前的文件偏移量只读本次新增行。
只取 contents 条目，不取 comments 文件。

合规立场：creator_hash / 脱敏 nickname 一律不映射；creator 模式下 author
从源注册表的 source.name 恢复（见设计文档 §6）。
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path

from acquisition.adapters.base import (
    Adapter,
    AdapterError,
    LoginInvalidError,
    LoginRequiredError,
)
from acquisition.models import Item
from acquisition.sources import Source
from agent_reach import mediacrawler_bridge as bridge

_DEFAULT_TIMEOUT = 900  # 子进程超时（秒），extra_args.timeout 可覆盖

# mc 平台码 → (产物目录名, Item.platform 规范名)
# 目录名读各 store/_store_impl.py 的 AsyncFileWriter(platform=...) 确认
_PLATFORMS = {
    "wb": ("weibo", "weibo"),
    "dy": ("douyin", "douyin"),
    "ks": ("kuaishou", "kuaishou"),
    "bili": ("bili", "bilibili"),
    "tieba": ("tieba", "tieba"),
    "zhihu": ("zhihu", "zhihu"),
    "xhs": ("xhs", "xiaohongshu"),
}

# 各平台内容产物字段 → Item 字段（读各 store/<platform>/__init__.py 确认；
# tieba/zhihu 是 pydantic model，读 model/m_baidu_tieba.py / m_zhihu.py）
_FIELD_MAPS = {
    "wb": {
        "item_id": "note_id",
        "content": "content",
        "published_at": "create_date_time",
        "url": "note_url",
        "metrics": {
            "liked": "liked_count",
            "comments": "comments_count",
            "shared": "shared_count",
        },
    },
    "dy": {
        "item_id": "aweme_id",
        "content": "desc",
        "title": "title",
        "published_at": "create_time",
        "url": "aweme_url",
        "metrics": {
            "liked": "liked_count",
            "collected": "collected_count",
            "comments": "comment_count",
            "shared": "share_count",
        },
    },
    "ks": {
        "item_id": "video_id",
        "content": "desc",
        "title": "title",
        "published_at": "create_time",
        "url": "video_url",
        "metrics": {"liked": "liked_count", "views": "viewd_count"},
    },
    "bili": {
        "item_id": "video_id",
        "content": "desc",
        "title": "title",
        "published_at": "create_time",
        "url": "video_url",
        "metrics": {
            "liked": "liked_count",
            "play": "video_play_count",
            "favorite": "video_favorite_count",
            "shared": "video_share_count",
            "coin": "video_coin_count",
            "danmaku": "video_danmaku",
            "comments": "video_comment",
        },
    },
    "tieba": {
        "item_id": "note_id",
        "content": "desc",
        "title": "title",
        "published_at": "publish_time",
        "url": "note_url",
        "metrics": {"replies": "total_replay_num"},
    },
    "zhihu": {
        "item_id": "content_id",
        "content": "content_text",
        "title": "title",
        "published_at": "created_time",
        "url": "content_url",
        "metrics": {"voteup": "voteup_count", "comments": "comment_count"},
    },
    "xhs": {
        "item_id": "note_id",
        "content": "desc",
        "title": "title",
        "published_at": "time",
        "url": "note_url",
        "metrics": {
            "liked": "liked_count",
            "collected": "collected_count",
            "comments": "comment_count",
            "shared": "share_count",
        },
    },
}

# 产物为空时在输出里识别登录态失效的特征（读 media_platform/*/login.py、
# client.py 的日志措辞确认；命中即视为登录失效，宁可误报不漏报）
_LOGIN_MARKERS = (
    "登录",
    "login",
    "验证",
    "扫码",
    "captcha",
    "challenge",
    "slider",
)

# extra_args 里被本适配器/路由器消费、不透传给 MediaCrawler 的键
_RESERVED_EXTRA_KEYS = {"timeout", "jitter", "keywords", "get_comment"}


def _ensure_ready() -> None:
    """mediacrawler 子树与依赖的就绪检查，不就绪抛 AdapterError。"""
    if not bridge.mediacrawler_available():
        raise AdapterError(f"MediaCrawler 未集成（mediacrawler/ 目录缺失）。{bridge.INSTALL_HINT}")
    if not bridge.deps_ready():
        raise AdapterError(bridge.INSTALL_HINT)


def _build_args(source: Source) -> list:
    """把 Source 翻译成 MediaCrawler 命令行参数（不含 --platform）。"""
    extra = dict(source.extra_args)
    crawler_type = source.crawler_type or "creator"
    args = ["--type", crawler_type]
    if crawler_type == "creator" and source.creator_id:
        args += ["--creator_id", str(source.creator_id)]
    if crawler_type == "search":
        keywords = extra.pop("keywords", "")
        if keywords:
            args += ["--keywords", str(keywords)]
    args += ["--crawler_max_notes_count", str(source.max_items)]
    # 默认不抓评论（第三方用户保持脱敏是特性），extra_args.get_comment 可覆盖
    args += ["--get_comment", str(extra.pop("get_comment", "no"))]
    args += ["--save_data_option", "jsonl"]
    # 其余 extra_args 原样透传（True 视为纯开关，False/None 忽略）
    for key in sorted(extra):
        if key in _RESERVED_EXTRA_KEYS:
            continue
        value = extra[key]
        if value is True:
            args.append(f"--{key}")
        elif value is False or value is None:
            continue
        else:
            args += [f"--{key}", str(value)]
    return args


def _products_path(source: Source, today: str) -> Path:
    """本次抓取对应的内容产物 jsonl 路径。"""
    writer_dir = _PLATFORMS[source.platform][0]
    crawler_type = source.crawler_type or "creator"
    return (
        bridge.MEDIACRAWLER_DIR
        / "data"
        / writer_dir
        / "jsonl"
        / f"{crawler_type}_contents_{today}.jsonl"
    )


def _read_new_lines(path: Path, offset: int) -> list:
    """从偏移量起读取产物新增行并解析为 dict 列表（坏行跳过）。"""
    if not path.is_file():
        return []
    with open(path, "rb") as handle:
        handle.seek(offset)
        payload = handle.read().decode("utf-8", errors="replace")
    items = []
    for line in payload.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            items.append(entry)
    return items


def _has_login_marker(output: str) -> bool:
    lowered = output.lower()
    return any(marker in lowered for marker in _LOGIN_MARKERS)


def _map_item(raw: dict, source: Source) -> Item:
    """单条产物 → 统一 Item。author 从源注册表恢复，不碰匿名化字段。"""
    writer_dir, canonical = _PLATFORMS[source.platform]
    fmap = _FIELD_MAPS[source.platform]
    metrics = {
        name: str(raw[field])
        for name, field in fmap["metrics"].items()
        if raw.get(field) not in (None, "")
    }
    title = raw.get(fmap["title"]) if "title" in fmap else None
    return Item(
        source_id=source.id,
        lane=source.lane,
        platform=canonical,
        item_id=str(raw.get(fmap["item_id"]) or ""),
        author=source.name,
        title=str(title) if title else None,
        content=str(raw.get(fmap["content"]) or ""),
        url=str(raw.get(fmap["url"]) or ""),
        published_at=str(raw.get(fmap["published_at"]) or ""),
        metrics=metrics,
        snapshot=False,
    )


class MediaCrawlerAdapter(Adapter):
    """子进程调用 MediaCrawler 并映射产物。"""

    def __init__(self, runner=None) -> None:
        # runner 可注入（测试 mock subprocess.run）
        self._runner = runner or subprocess.run

    def fetch(self, source: Source) -> list:
        if source.platform not in _PLATFORMS:
            raise AdapterError(f"未知 mediacrawler 平台码：{source.platform!r}")
        _ensure_ready()
        if not bridge.has_login_state(source.platform):
            raise LoginRequiredError(
                f"平台 {source.platform} 无登录态缓存。请先登录："
                f"agent-reach crawl {source.platform} --lt qrcode（扫码一次）"
            )

        args = _build_args(source)
        cmd = bridge.build_command(source.platform, args)
        today = datetime.now().strftime("%Y-%m-%d")
        products = _products_path(source, today)
        offset = products.stat().st_size if products.is_file() else 0
        timeout = int(source.extra_args.get("timeout", _DEFAULT_TIMEOUT))

        try:
            proc = self._runner(
                cmd,
                cwd=str(bridge.MEDIACRAWLER_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterError(f"MediaCrawler 超时（{timeout}s）：{source.id}") from exc

        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-500:]
            raise AdapterError(f"MediaCrawler 退出码 {proc.returncode}：{tail}")

        raws = _read_new_lines(products, offset)
        if not raws:
            if _has_login_marker(output):
                raise LoginInvalidError(
                    f"产物为空且日志含登录/验证特征，判定登录态失效：{source.id}"
                )
            return []
        return [_map_item(raw, source) for raw in raws]


def fetch(source: Source) -> list:
    """模块级便捷入口（路由器按 kind 分发用）。"""
    return MediaCrawlerAdapter().fetch(source)
