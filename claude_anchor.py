import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"
TIMESTAMP_FILE = Path(__file__).parent / "last_run_timestamp.txt"
LOG_FILE = Path(__file__).parent / "claude_anchor.log"

DEFAULT_CONFIG = {
    "http_proxy": "",
    "https_proxy": "",
    "no_proxy": "localhost,127.0.0.1",
    "webhook_url": "",
}


def load_config(path=CONFIG_PATH):
    p = Path(path)
    if not p.exists():
        return DEFAULT_CONFIG.copy()
    with open(p) as f:
        data = json.load(f)
    return {**DEFAULT_CONFIG, **data}


def build_env(config):
    env = os.environ.copy()
    if config.get("https_proxy"):
        env["HTTPS_PROXY"] = config["https_proxy"]
        env["HTTP_PROXY"] = config.get("http_proxy", config["https_proxy"])
        env["NO_PROXY"] = config.get("no_proxy", "localhost,127.0.0.1")
    return env
