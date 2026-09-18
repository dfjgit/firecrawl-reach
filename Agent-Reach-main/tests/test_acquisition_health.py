# -*- coding: utf-8 -*-
"""health：blocked.json 的最小读取。"""

import json

from acquisition.health import blocked_sources


def test_missing_file_means_no_blocked(tmp_path):
    assert blocked_sources(tmp_path / "blocked.json") == set()


def test_list_format(tmp_path):
    path = tmp_path / "blocked.json"
    path.write_text(json.dumps(["a", "b"]), encoding="utf-8")
    assert blocked_sources(path) == {"a", "b"}


def test_dict_with_blocked_key(tmp_path):
    path = tmp_path / "blocked.json"
    path.write_text(json.dumps({"blocked": ["a"]}), encoding="utf-8")
    assert blocked_sources(path) == {"a"}


def test_dict_of_objects_format(tmp_path):
    path = tmp_path / "blocked.json"
    path.write_text(json.dumps({"a": {"reason": "登录态失效"}}), encoding="utf-8")
    assert blocked_sources(path) == {"a"}


def test_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "blocked.json"
    path.write_text("not json", encoding="utf-8")
    assert blocked_sources(path) == set()
