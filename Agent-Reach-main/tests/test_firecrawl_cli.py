# -*- coding: utf-8 -*-
"""firecrawl 站点级 CLI：map / crawl-site / batch / search。"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent_reach import cli
from agent_reach.firecrawl_client import FirecrawlUnavailableError


def _client(**overrides):
    client = MagicMock()
    for name, value in overrides.items():
        getattr(client, name).return_value = value
    return client


class TestDisabled:
    def test_exits_when_not_enabled(self, capsys):
        # cli.py 模块级未 import Config（_cmd_doctor 是函数内 import），
        # 所以 patch 目标必须是 agent_reach.config.Config
        with patch("agent_reach.config.Config.get", return_value=""):
            with pytest.raises(SystemExit):
                cli._cmd_map(SimpleNamespace(url="https://a.com", limit=None))
        assert "未启用" in capsys.readouterr().out


class TestMap:
    def test_prints_links(self, capsys):
        client = _client(map=["https://a.com/1", "https://a.com/2"])
        with patch.object(cli, "_firecrawl_client_or_exit", return_value=client):
            cli._cmd_map(SimpleNamespace(url="https://a.com", limit=10))
        out = capsys.readouterr().out
        assert "https://a.com/1" in out and "https://a.com/2" in out
        client.map.assert_called_once_with("https://a.com", limit=10)


class TestCrawlSite:
    def test_writes_jsonl(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("AGENT_REACH_HOME", str(tmp_path))
        pages = [{"markdown": "# p1", "metadata": {"sourceURL": "https://a.com/1"}}]
        client = _client(crawl=pages)
        with patch.object(cli, "_firecrawl_client_or_exit", return_value=client):
            cli._cmd_crawl_site(
                SimpleNamespace(url="https://a.com", limit=5, max_wait=60)
            )
        out = capsys.readouterr().out
        assert "抓取 1 页" in out
        written = list((tmp_path / "acquisition" / "data" / "firecrawl").glob("*.jsonl"))
        assert len(written) == 1
        assert json.loads(written[0].read_text(encoding="utf-8").strip()) == pages[0]
        client.crawl.assert_called_once_with("https://a.com", limit=5, max_wait=60)


class TestBatch:
    def test_reads_file_and_writes_jsonl(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("AGENT_REACH_HOME", str(tmp_path))
        url_file = tmp_path / "urls.txt"
        url_file.write_text("https://a.com\nhttps://b.com\n", encoding="utf-8")
        client = _client(batch_scrape=[{"markdown": "x"}, {"markdown": "y"}])
        with patch.object(cli, "_firecrawl_client_or_exit", return_value=client):
            cli._cmd_batch(SimpleNamespace(file=str(url_file)))
        out = capsys.readouterr().out
        assert "抓取 2/2 页" in out
        client.batch_scrape.assert_called_once_with(["https://a.com", "https://b.com"])

    def test_empty_file_exits(self, tmp_path):
        url_file = tmp_path / "urls.txt"
        url_file.write_text("", encoding="utf-8")
        with patch.object(cli, "_firecrawl_client_or_exit", return_value=_client()):
            with pytest.raises(SystemExit):
                cli._cmd_batch(SimpleNamespace(file=str(url_file)))

    def test_missing_file_exits_with_message(self, capsys, tmp_path):
        missing = tmp_path / "nope.txt"
        with patch.object(cli, "_firecrawl_client_or_exit", return_value=_client()):
            with pytest.raises(SystemExit):
                cli._cmd_batch(SimpleNamespace(file=str(missing)))
        assert "文件不存在" in capsys.readouterr().out


class TestSearch:
    def test_prints_results(self, capsys):
        client = _client(search=[{"title": "T", "url": "https://a.com"}])
        with patch.object(cli, "_firecrawl_client_or_exit", return_value=client):
            cli._cmd_search(SimpleNamespace(query="q", limit=3))
        assert "T" in capsys.readouterr().out
        client.search.assert_called_once_with("q", limit=3)


class TestUnreachable:
    def test_ping_failure_exits_with_hint(self, capsys):
        with patch("agent_reach.config.Config.get", side_effect=lambda k, d=None: {
            "firecrawl_enabled": "true"}.get(k, d)), \
             patch("agent_reach.firecrawl_client.FirecrawlClient.ping",
                   side_effect=FirecrawlUnavailableError("refused")):
            with pytest.raises(SystemExit):
                cli._cmd_map(SimpleNamespace(url="https://a.com", limit=None))
        assert "docker compose up" in capsys.readouterr().out
