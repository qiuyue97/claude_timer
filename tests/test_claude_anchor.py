import json
import os
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from claude_anchor import (
    load_config, build_env, DEFAULT_CONFIG,
    save_timestamp, load_timestamp,
)


# ---------- Task 2: config + proxy ----------

def test_load_config_returns_defaults_when_file_missing(tmp_path):
    result = load_config(tmp_path / "nonexistent.json")
    assert result == DEFAULT_CONFIG


def test_load_config_merges_over_defaults(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"https_proxy": "http://127.0.0.1:7890"}))
    result = load_config(cfg_path)
    assert result["https_proxy"] == "http://127.0.0.1:7890"
    assert result["webhook_url"] == ""


def test_build_env_injects_proxy_vars():
    config = {
        "http_proxy": "http://127.0.0.1:7890",
        "https_proxy": "http://127.0.0.1:7890",
        "no_proxy": "localhost,127.0.0.1",
    }
    env = build_env(config)
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert env["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert env["NO_PROXY"] == "localhost,127.0.0.1"


def test_build_env_skips_proxy_when_empty():
    config = {"http_proxy": "", "https_proxy": "", "no_proxy": "localhost,127.0.0.1"}
    env = build_env(config)
    assert env.get("HTTPS_PROXY") == os.environ.get("HTTPS_PROXY")


# ---------- Task 3: timestamp ----------

def test_save_and_load_timestamp_roundtrip(tmp_path):
    ts = datetime(2026, 5, 7, 9, 0, 0)
    ts_file = tmp_path / "ts.txt"
    save_timestamp(ts, ts_file)
    loaded = load_timestamp(ts_file)
    assert loaded == ts


def test_load_timestamp_returns_none_when_missing(tmp_path):
    result = load_timestamp(tmp_path / "nonexistent.txt")
    assert result is None


def test_load_timestamp_returns_none_on_corrupt_file(tmp_path):
    ts_file = tmp_path / "ts.txt"
    ts_file.write_text("not-a-date")
    result = load_timestamp(ts_file)
    assert result is None
