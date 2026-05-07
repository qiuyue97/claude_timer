import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from claude_anchor import load_config, build_env, DEFAULT_CONFIG


def test_load_config_returns_defaults_when_file_missing(tmp_path):
    result = load_config(tmp_path / "nonexistent.json")
    assert result == DEFAULT_CONFIG


def test_load_config_merges_over_defaults(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"https_proxy": "http://127.0.0.1:7890"}))
    result = load_config(cfg_path)
    assert result["https_proxy"] == "http://127.0.0.1:7890"
    assert result["webhook_url"] == ""  # default preserved


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
