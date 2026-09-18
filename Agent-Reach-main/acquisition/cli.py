# -*- coding: utf-8 -*-
"""acquire 命令行：run / latest / sources / blocked / unblock 子命令。

无状态 CLI：被调用一次跑一轮，退出码有任何 error 为 1，否则 0。
定时触发由上游 Agent 负责，本模块不含调度器。
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

from acquisition import health, runs_log_path
from acquisition.health import blocked_sources
from acquisition.router import format_summary, run_round
from acquisition.sources import SourceConfigError, load_sources, parse_interval
from acquisition.store import Store


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acquire",
        description="采集模块：统一抓取 sources.yaml 里注册的源（无状态，跑一轮即退）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="跑一轮采集")
    run.add_argument("--source", metavar="ID", help="只跑指定源")
    run.add_argument("--sources", metavar="PATH", help="源注册表路径（覆盖默认）")
    run.add_argument("--force", action="store_true", help="忽略 interval 节流")

    latest = sub.add_parser("latest", help="查看最新条目")
    latest.add_argument("--source", metavar="ID", help="只看指定源")
    latest.add_argument("-n", type=int, default=20, help="最多显示条数（默认 20）")
    latest.add_argument("--since", metavar="SPAN", help="只看最近时段，如 1h/30m")
    latest.add_argument("--sources", metavar="PATH", help="源注册表路径（覆盖默认）")

    listing = sub.add_parser("sources", help="列出源及状态")
    listing.add_argument("--sources", metavar="PATH", help="源注册表路径（覆盖默认）")

    sub.add_parser("blocked", help="列出 blocked（登录态失效）的源")

    unblock = sub.add_parser("unblock", help="把源移出 blocked（重新登录后用）")
    unblock.add_argument("source_id", help="要解除 blocked 的源 id")
    return parser


def _cmd_run(args) -> int:
    try:
        sources = load_sources(args.sources)
    except SourceConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1
    if args.source:
        sources = [s for s in sources if s.id == args.source]
        if not sources:
            print(f"源不存在：{args.source}", file=sys.stderr)
            return 1

    store = Store()
    try:
        summaries = run_round(sources, store, force=args.force)
    finally:
        store.close()

    lines = [format_summary(entry) for entry in summaries]
    for line in lines:
        print(line)
    _append_runs_log(lines)
    blocked = [entry["source_id"] for entry in summaries if entry["status"] == "blocked"]
    if blocked:
        print(
            f"[!] {len(blocked)} 个源登录态失效（{'、'.join(blocked)}）；"
            "重新登录后用 `acquire unblock <source_id>` 恢复，详见 `acquire blocked`"
        )
    return 1 if any(entry["status"] == "error" for entry in summaries) else 0


def _append_runs_log(lines: list) -> None:
    """每轮汇总追加到 runs.log（写失败不影响本轮结果）。"""
    try:
        log_path = runs_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        with open(log_path, "a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(f"{stamp} {line}\n")
    except OSError:
        pass


def _cmd_latest(args) -> int:
    since = None
    if args.since:
        try:
            span = parse_interval(args.since)
        except SourceConfigError as exc:
            print(f"--since 格式错误：{exc}", file=sys.stderr)
            return 1
        since = datetime.now(timezone.utc) - timedelta(seconds=span)

    store = Store()
    try:
        items = store.read_items(source_id=args.source, since=since)
    finally:
        store.close()
    items = items[-args.n :] if args.n > 0 else items
    if not items:
        print("（暂无条目）")
        return 0
    for item in items:
        title = item.title or item.content.replace("\n", " ")[:60]
        stamp = item.published_at or item.fetched_at
        suffix = " [快照]" if item.snapshot else ""
        print(f"[{stamp}] ({item.source_id}) {title}{suffix}")
        if item.url:
            print(f"    {item.url}")
    return 0


def _cmd_sources(args) -> int:
    try:
        sources = load_sources(args.sources)
    except SourceConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1
    store = Store()
    try:
        blocked = blocked_sources()
        for source in sources:
            last = store.last_fetched_at(source.id)
            last_text = last.isoformat() if last else "从未抓取"
            flags = []
            if not source.enabled:
                flags.append("已禁用")
            if source.id in blocked:
                flags.append("blocked")
            flag_text = f" [{'/'.join(flags)}]" if flags else ""
            print(
                f"{source.id} | {source.name} | lane={source.lane} kind={source.kind}"
                f" | interval={source.interval}s max_items={source.max_items}"
                f" | 上次抓取：{last_text}{flag_text}"
            )
    finally:
        store.close()
    return 0


def _cmd_blocked(args) -> int:
    entries = health.blocked_entries()
    if not entries:
        print("（无 blocked 源）")
        return 0
    for entry in entries:
        platform = entry.get("platform") or "-"
        blocked_at = entry.get("blocked_at") or "-"
        reason = entry.get("reason") or ""
        print(f"{entry['source_id']} | platform={platform} | {blocked_at} | {reason}")
    return 0


def _cmd_unblock(args) -> int:
    if health.unblock_source(args.source_id):
        print(f"已解除 blocked：{args.source_id}")
    else:
        print(f"源不在 blocked 列表中：{args.source_id}")
    return 0


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "latest":
        return _cmd_latest(args)
    if args.command == "sources":
        return _cmd_sources(args)
    if args.command == "blocked":
        return _cmd_blocked(args)
    if args.command == "unblock":
        return _cmd_unblock(args)
    parser.error("未知命令")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
