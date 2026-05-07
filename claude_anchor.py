import json
import os
from datetime import datetime, timedelta
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


def save_timestamp(ts=None, path=TIMESTAMP_FILE):
    ts = ts or datetime.now()
    Path(path).write_text(ts.isoformat())


def load_timestamp(path=TIMESTAMP_FILE):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return datetime.fromisoformat(p.read_text().strip())
    except ValueError:
        return None


PING_INTERVAL = timedelta(hours=5)


def calculate_next_ping(last_ping, daily_reset_time, now=None):
    """Return the datetime of the next scheduled ping.

    - No last_ping: use today's reset (or tomorrow's if already past).
    - last_ping + 5h < next reset: use the interval.
    - Otherwise: use the next reset (don't miss the daily anchor).
    """
    now = now or datetime.now()
    reset_h, reset_m = daily_reset_time
    today_reset = now.replace(hour=reset_h, minute=reset_m, second=0, microsecond=0)
    next_reset = today_reset if now < today_reset else today_reset + timedelta(days=1)

    if last_ping is None:
        return next_reset

    next_interval = last_ping + PING_INTERVAL
    return next_interval if next_interval < next_reset else next_reset
