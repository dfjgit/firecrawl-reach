# -*- coding: utf-8 -*-
"""CLI：run / latest / sources（HOME 已由 conftest 隔离到 tmp_path）。"""

import json

import acquisition.cli as cli
import acquisition.router as router
from acquisition import acquisition_dir
from acquisition.models import Item

SOURCES_YAML = """
- id: kr36-rss
  name: 36氪
  lane: news
  kind: rss
  url: https://36kr.com/feed
  interval: 15m
- id: auth-src
  name: 登录源
  lane: auth
  kind: twitter-cli
  interval: 30m
"""


def _write_sources(tmp_path, text=SOURCES_YAML):
    path = tmp_path / "sources.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _fake_items(source):
    return [
        Item(
            source_id=source.id,
            lane=source.lane,
            platform="rss",
            item_id=f"{source.id}-1",
            title=f"{source.name}快讯",
            content="正文",
            url="https://example.com/p/1",
            published_at="2026-08-03 08:00",
        )
    ]


def test_run_prints_summary_and_exit_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(router.ADAPTERS, "rss", _fake_items)
    # 移除 twitter-cli 适配器，让 auth-src 走"未知 kind"兜底（结果确定，不依赖本机是否装 twitter）
    monkeypatch.delitem(router.ADAPTERS, "twitter-cli")
    code = cli.main(["run", "--sources", str(_write_sources(tmp_path))])
    out = capsys.readouterr().out
    assert code == 1  # auth-src 无适配器记 error → 退出码 1
    assert "kr36-rss → ok new 0 / dup 0" in out  # 首跑 baseline
    assert "auth-src → error" in out and "未知 kind" in out


def test_run_second_time_all_dup_exit_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(router.ADAPTERS, "rss", _fake_items)
    path = str(_write_sources(tmp_path, SOURCES_YAML.replace("- id: auth-src", "- id: auth-src\n  enabled: false")))
    assert cli.main(["run", "--sources", path]) == 0
    assert cli.main(["run", "--sources", path, "--force"]) == 0
    out = capsys.readouterr().out
    assert "kr36-rss → ok new 0 / dup 1" in out


def test_run_throttle_skips_without_force(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(router.ADAPTERS, "rss", _fake_items)
    path = str(_write_sources(tmp_path))
    cli.main(["run", "--sources", path, "--source", "kr36-rss"])
    code = cli.main(["run", "--sources", path, "--source", "kr36-rss"])
    out = capsys.readouterr().out
    assert code == 0
    assert "kr36-rss → skipped" in out and "节流" in out


def test_run_unknown_source_exit_one(tmp_path, capsys):
    code = cli.main(["run", "--sources", str(_write_sources(tmp_path)), "--source", "nope"])
    assert code == 1
    assert "源不存在" in capsys.readouterr().err


def test_run_missing_sources_file_exit_one(tmp_path, capsys):
    code = cli.main(["run", "--sources", str(tmp_path / "none.yaml")])
    assert code == 1
    assert "配置错误" in capsys.readouterr().err


def test_run_writes_runs_log(tmp_path, monkeypatch):
    monkeypatch.setitem(router.ADAPTERS, "rss", _fake_items)
    cli.main(["run", "--sources", str(_write_sources(tmp_path)), "--source", "kr36-rss"])
    log = acquisition_dir() / "runs.log"
    assert log.is_file()
    assert "kr36-rss → ok" in log.read_text(encoding="utf-8")


def test_latest_shows_new_items(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(router.ADAPTERS, "rss", _fake_items)
    path = str(_write_sources(tmp_path))
    cli.main(["run", "--sources", path, "--source", "kr36-rss"])
    # 首跑 baseline 无 new → latest 为空
    assert cli.main(["latest", "--sources", path]) == 0
    assert "暂无条目" in capsys.readouterr().out
    # 塞入新条目再跑一轮
    monkeypatch.setitem(
        router.ADAPTERS,
        "rss",
        lambda s: _fake_items(s)[:1] and [
            Item(source_id=s.id, lane="news", platform="rss", item_id="new-1",
                 title="新快讯", content="新内容", url="https://example.com/p/2")
        ],
    )
    cli.main(["run", "--sources", path, "--source", "kr36-rss", "--force"])
    assert cli.main(["latest", "--sources", path, "-n", "5"]) == 0
    out = capsys.readouterr().out
    assert "新快讯" in out
    assert "https://example.com/p/2" in out


def test_latest_bad_since_exit_one(tmp_path, capsys):
    code = cli.main(["latest", "--sources", str(_write_sources(tmp_path)), "--since", "xyz"])
    assert code == 1
    assert "--since 格式错误" in capsys.readouterr().err


def test_sources_command_lists_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(router.ADAPTERS, "rss", _fake_items)
    path = str(_write_sources(tmp_path))
    cli.main(["run", "--sources", path, "--source", "kr36-rss"])
    assert cli.main(["sources", "--sources", path]) == 0
    out = capsys.readouterr().out
    assert "kr36-rss" in out and "lane=news kind=rss" in out and "interval=900s" in out
    assert "auth-src" in out and "从未抓取" in out


def test_sources_command_marks_blocked(tmp_path, capsys):
    path = str(_write_sources(tmp_path))
    blocked_file = acquisition_dir() / "blocked.json"
    blocked_file.parent.mkdir(parents=True, exist_ok=True)
    blocked_file.write_text(json.dumps(["kr36-rss"]), encoding="utf-8")
    assert cli.main(["sources", "--sources", path]) == 0
    out = capsys.readouterr().out
    assert "kr36-rss" in out and "[blocked]" in out
