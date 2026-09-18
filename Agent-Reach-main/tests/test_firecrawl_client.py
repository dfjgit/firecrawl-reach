# -*- coding: utf-8 -*-
"""FirecrawlClient：自托管 Firecrawl 栈的薄 REST 客户端。"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from agent_reach.firecrawl_client import (
    FirecrawlAPIError,
    FirecrawlClient,
    FirecrawlUnavailableError,
    firecrawl_enabled,
)


def _resp(payload, ok=True, status_code=200, text=""):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = payload
    return resp


class TestScrape:
    def test_returns_data(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post:
            post.return_value = _resp({"success": True, "data": {"markdown": "# hi"}})
            data = FirecrawlClient().scrape("https://example.com")
        assert data == {"markdown": "# hi"}
        args, kwargs = post.call_args
        assert args[0] == "http://localhost:3002/v1/scrape"
        assert kwargs["json"] == {"url": "https://example.com", "formats": ["markdown"]}

    def test_api_key_header(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post:
            post.return_value = _resp({"success": True, "data": {}})
            FirecrawlClient(api_key="fc-key").scrape("https://example.com")
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer fc-key"

    def test_http_error_raises_api_error(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post:
            post.return_value = _resp({}, ok=False, status_code=500, text="boom")
            with pytest.raises(FirecrawlAPIError, match="HTTP 500"):
                FirecrawlClient().scrape("https://example.com")

    def test_network_error_raises_unavailable(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post:
            post.side_effect = requests.exceptions.ConnectionError("refused")
            with pytest.raises(FirecrawlUnavailableError):
                FirecrawlClient().scrape("https://example.com")

    def test_success_false_raises_api_error(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post:
            post.return_value = _resp({"success": False, "error": "blocked"})
            with pytest.raises(FirecrawlAPIError, match="success=false"):
                FirecrawlClient().scrape("https://example.com")


class TestMap:
    def test_returns_links(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post:
            post.return_value = _resp({"success": True, "links": ["https://a.com/1"]})
            links = FirecrawlClient().map("https://a.com", limit=10)
        assert links == ["https://a.com/1"]
        assert post.call_args.kwargs["json"] == {"url": "https://a.com", "limit": 10}


class TestCrawl:
    def test_polls_until_completed(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post, \
             patch("agent_reach.firecrawl_client.requests.get") as get:
            post.return_value = _resp({"success": True, "id": "job-1"})
            get.return_value = _resp(
                {"status": "completed", "data": [{"markdown": "p1"}]}
            )
            pages = FirecrawlClient().crawl("https://a.com", limit=5)
        assert pages == [{"markdown": "p1"}]
        assert get.call_args.args[0] == "http://localhost:3002/v1/crawl/job-1"

    def test_failed_job_raises(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post, \
             patch("agent_reach.firecrawl_client.requests.get") as get:
            post.return_value = _resp({"success": True, "id": "job-1"})
            get.return_value = _resp({"status": "failed"})
            with pytest.raises(FirecrawlAPIError, match="failed"):
                FirecrawlClient().crawl("https://a.com")

    def test_timeout_raises_unavailable(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post, \
             patch("agent_reach.firecrawl_client.requests.get") as get, \
             patch("agent_reach.firecrawl_client.time.sleep"):
            post.return_value = _resp({"success": True, "id": "job-1"})
            get.return_value = _resp({"status": "scraping", "data": []})
            with pytest.raises(FirecrawlUnavailableError, match="超时"):
                FirecrawlClient().crawl("https://a.com", max_wait=0, poll_interval=0)


class TestBatchAndSearch:
    def test_batch_scrape(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post, \
             patch("agent_reach.firecrawl_client.requests.get") as get:
            post.return_value = _resp({"success": True, "id": "batch-1"})
            get.return_value = _resp({"status": "completed", "data": [{"markdown": "x"}]})
            pages = FirecrawlClient().batch_scrape(["https://a.com", "https://b.com"])
        assert pages == [{"markdown": "x"}]
        assert post.call_args.kwargs["json"]["urls"] == ["https://a.com", "https://b.com"]

    def test_search(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post:
            post.return_value = _resp({"success": True, "data": [{"url": "u", "title": "t"}]})
            results = FirecrawlClient().search("query", limit=3)
        assert results == [{"url": "u", "title": "t"}]


class TestFromConfig:
    def test_reads_config_keys(self):
        config = {"firecrawl_url": "http://fc:3002/", "firecrawl_api_key": "k"}
        client = FirecrawlClient.from_config(config)
        assert client.base_url == "http://fc:3002"
        assert client.api_key == "k"

    def test_defaults(self):
        client = FirecrawlClient.from_config({})
        assert client.base_url == "http://localhost:3002"
        assert client.api_key is None

    @pytest.mark.parametrize("dirty", ["abc", "", "30.5", None])
    def test_dirty_timeout_falls_back_to_default(self, dirty):
        client = FirecrawlClient.from_config({"firecrawl_timeout": dirty})
        assert client.timeout == 30

    def test_valid_timeout(self):
        client = FirecrawlClient.from_config({"firecrawl_timeout": "10"})
        assert client.timeout == 10


class TestFirecrawlEnabled:
    def test_true_with_whitespace(self):
        assert firecrawl_enabled({"firecrawl_enabled": " true"}) is True

    def test_empty_is_false(self):
        assert firecrawl_enabled({}) is False


class TestParseNonJson:
    def test_non_json_200_raises_unavailable(self):
        resp = _resp({})
        resp.json.side_effect = requests.exceptions.JSONDecodeError("msg", "doc", 0)
        with patch("agent_reach.firecrawl_client.requests.post") as post:
            post.return_value = resp
            with pytest.raises(FirecrawlUnavailableError, match="非 JSON"):
                FirecrawlClient().scrape("https://example.com")


class TestPing:
    def test_reachable_does_not_raise(self):
        with patch("agent_reach.firecrawl_client.requests.get") as get:
            get.return_value = _resp({})
            FirecrawlClient().ping()
        assert get.call_args.args[0] == "http://localhost:3002/"

    def test_connection_error_raises_unavailable(self):
        with patch("agent_reach.firecrawl_client.requests.get") as get:
            get.side_effect = requests.exceptions.ConnectionError("refused")
            with pytest.raises(FirecrawlUnavailableError):
                FirecrawlClient().ping()


class TestFailedJobError:
    def test_error_field_in_message(self):
        with patch("agent_reach.firecrawl_client.requests.post") as post, \
             patch("agent_reach.firecrawl_client.requests.get") as get:
            post.return_value = _resp({"success": True, "id": "job-1"})
            get.return_value = _resp({"status": "failed", "error": "blocked by robots"})
            with pytest.raises(FirecrawlAPIError, match="blocked by robots"):
                FirecrawlClient().crawl("https://a.com")
