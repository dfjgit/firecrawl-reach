# -*- coding: utf-8 -*-
"""源注册表：加载、校验、interval 解析。"""

import pytest

from acquisition.sources import SourceConfigError, load_sources, parse_interval

VALID_YAML = """
- id: kr36-rss
  name: 36氪
  lane: news
  kind: rss
  url: https://36kr.com/feed
  interval: 15m
- id: cls-telegraph
  name: 财联社电报
  lane: news
  kind: stealth
  url: https://www.cls.cn/telegraph
  interval: 5m
  max_items: 30
  extra_args:
    item_pattern: '^\\d{2}:\\d{2}'
"""


def _write(tmp_path, text):
    path = tmp_path / "sources.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ── interval 解析 ─────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [("30s", 30), ("5m", 300), ("1h", 3600), ("2h", 7200), ("1d", 86400), (600, 600)],
)
def test_parse_interval(value, expected):
    assert parse_interval(value) == expected


@pytest.mark.parametrize("value", ["5x", "abc", 0, -3, True, ""])
def test_parse_interval_rejects_bad_values(value):
    with pytest.raises(SourceConfigError):
        parse_interval(value)


# ── 加载与校验 ────────────────────────────────────


def test_load_valid_sources(tmp_path):
    sources = load_sources(_write(tmp_path, VALID_YAML))
    assert [s.id for s in sources] == ["kr36-rss", "cls-telegraph"]
    rss, stealth = sources
    assert rss.interval == 900
    assert rss.max_items == 30
    assert rss.enabled is True
    assert stealth.interval == 300
    assert stealth.extra_args["item_pattern"] == r"^\d{2}:\d{2}"


def test_missing_file_raises(tmp_path):
    with pytest.raises(SourceConfigError, match="不存在"):
        load_sources(tmp_path / "nope.yaml")


def test_empty_file_returns_empty_list(tmp_path):
    assert load_sources(_write(tmp_path, "")) == []


def test_top_level_must_be_list(tmp_path):
    with pytest.raises(SourceConfigError, match="顶层必须是列表"):
        load_sources(_write(tmp_path, "id: foo\n"))


@pytest.mark.parametrize(
    "entry,missing",
    [
        ("lane: news\n  kind: rss\n  url: http://x", "id"),
        ("id: a\n  kind: rss\n  url: http://x", "lane"),
        ("id: a\n  lane: news\n  url: http://x", "kind"),
    ],
)
def test_missing_required_field(tmp_path, entry, missing):
    with pytest.raises(SourceConfigError, match=f"缺少必填字段 '{missing}'"):
        load_sources(_write(tmp_path, f"- {entry}\n"))


def test_unknown_lane_rejected(tmp_path):
    text = "- id: a\n  lane: vip\n  kind: rss\n  url: http://x\n"
    with pytest.raises(SourceConfigError, match="未知 lane 'vip'"):
        load_sources(_write(tmp_path, text))


def test_unknown_kind_rejected(tmp_path):
    text = "- id: a\n  lane: news\n  kind: jina\n  url: http://x\n"
    with pytest.raises(SourceConfigError, match="未知 kind 'jina'"):
        load_sources(_write(tmp_path, text))


def test_rss_source_requires_url(tmp_path):
    text = "- id: a\n  lane: news\n  kind: rss\n"
    with pytest.raises(SourceConfigError, match="必须配置 url"):
        load_sources(_write(tmp_path, text))


def test_duplicate_id_rejected(tmp_path):
    text = (
        "- id: a\n  lane: news\n  kind: rss\n  url: http://x\n"
        "- id: a\n  lane: news\n  kind: rss\n  url: http://y\n"
    )
    with pytest.raises(SourceConfigError, match="id 重复"):
        load_sources(_write(tmp_path, text))


def test_defaults_applied(tmp_path):
    text = "- id: a\n  lane: news\n  kind: rss\n  url: http://x\n"
    (source,) = load_sources(_write(tmp_path, text))
    assert source.name == "a"
    assert source.interval == 300
    assert source.command == []
    assert source.extra_args == {}
