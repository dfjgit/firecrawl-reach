# -*- coding: utf-8 -*-
"""AGENT_REACH_HOME 环境变量：副本/原版用户级状态隔离。"""

from pathlib import Path

from acquisition import acquisition_dir
from agent_reach.config import Config


def test_config_uses_agent_reach_home(monkeypatch, tmp_path):
    override = tmp_path / "ar-home"
    monkeypatch.setenv("AGENT_REACH_HOME", str(override))
    config = Config(read_only=True)
    assert config.config_path == override / "config.yaml"


def test_config_default_home_without_env(monkeypatch):
    monkeypatch.delenv("AGENT_REACH_HOME", raising=False)
    config = Config(read_only=True)
    assert config.config_path == Path.home() / ".agent-reach" / "config.yaml"


def test_acquisition_dir_uses_agent_reach_home(monkeypatch, tmp_path):
    override = tmp_path / "ar-home"
    monkeypatch.setenv("AGENT_REACH_HOME", str(override))
    assert acquisition_dir() == override / "acquisition"


def test_acquisition_dir_default_without_env(monkeypatch):
    monkeypatch.delenv("AGENT_REACH_HOME", raising=False)
    assert acquisition_dir() == Path.home() / ".agent-reach" / "acquisition"
