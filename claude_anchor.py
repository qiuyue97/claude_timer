import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"
TIMESTAMP_FILE = Path(__file__).parent / "last_run_timestamp.txt"
LOG_FILE = Path(__file__).parent / "claude_anchor.log"

DEFAULT_CONFIG = {
    "http_proxy": "",
    "https_proxy": "",
    "no_proxy": "localhost,127.0.0.1",
    "webhook_url": "",
    "timing": {
        "preboot_lead": 120,      # seconds: spawn the TUI this long before fire_at
        "quiet_period": 2,        # seconds of no output ⇒ stream settled
        "response_timeout": 60,   # seconds: max wait for a reply after the message
        "reply_min_chars": 10,    # min visible reply chars (after fire) to count as a reply
        "exit_wait": 5,           # seconds after /exit before SIGTERM→SIGKILL
    },
}


def load_config(path=CONFIG_PATH):
    p = Path(path)
    if p.exists():
        with open(p) as f:
            data = json.load(f)
    else:
        data = {}
    merged = {**DEFAULT_CONFIG, **data}
    merged["timing"] = {**DEFAULT_CONFIG["timing"], **data.get("timing", {})}
    return merged


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
PING_BUFFER_BASE = 10  # buffer grows by 10s per ping each day: 10s, 20s, 30s, 40s


def calculate_next_ping(last_ping, daily_reset_time, now=None):
    """Return the datetime of the next scheduled ping.

    - No last_ping: use today's reset (or tomorrow's if already past).
    - The window opened by next_interval ends before next_reset: use next_interval.
    - Otherwise: use next_reset to preserve the daily anchor.
    """
    now = now or datetime.now()
    reset_h, reset_m = daily_reset_time
    today_reset = now.replace(hour=reset_h, minute=reset_m, second=0, microsecond=0)
    next_reset = today_reset if now < today_reset else today_reset + timedelta(days=1)

    if last_ping is None:
        return next_reset

    next_interval = last_ping + PING_INTERVAL
    # Only use next_interval if the window it opens (next_interval + 5h) ends by next_reset.
    # Otherwise the new window would overlap with the daily anchor ping, wasting it.
    return next_interval if next_interval + PING_INTERVAL <= next_reset else next_reset


def check_claude_available(env):
    try:
        result = subprocess.run(
            ["claude", "-v"],
            env=env,
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def send_ping(config, logger):
    env = build_env(config)
    logger.info("Sending ping to Claude...")
    try:
        result = subprocess.run(
            ["claude", "-p", "Hi", "--model", "haiku", "--no-session-persistence"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        logger.error("Ping timed out after 60s")
        return False
    if result.returncode == 0:
        logger.info("Ping successful")
        return True
    logger.error(f"Ping failed (exit {result.returncode}): {result.stderr.strip()}")
    return False


def send_webhook_alert(url, message, logger):
    if not url:
        return
    try:
        data = json.dumps({"text": message}).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        logger.warning(f"Webhook delivery failed: {exc}")


def setup_logging():
    logger = logging.getLogger("claude_anchor")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


def parse_daily_reset(value):
    try:
        h, m = value.split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        return (h, m)
    except (ValueError, AttributeError):
        raise argparse.ArgumentTypeError(
            f"Invalid time {value!r}. Expected HH:MM (e.g. 09:00)"
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Claude Session Anchor — pin Claude Code usage windows to a fixed daily time."
    )
    parser.add_argument(
        "--daily-reset",
        type=parse_daily_reset,
        metavar="HH:MM",
        help="Anchor time for the first ping each day (required in daemon mode)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Send one ping immediately and exit",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last saved timestamp instead of waiting for the next reset",
    )
    return parser


def main():
    parser = parse_args()
    args = parser.parse_args()

    if not args.once and not args.daily_reset:
        parser.print_help()
        print(
            "\nError: --daily-reset=HH:MM is required in daemon mode.",
            file=sys.stderr,
        )
        sys.exit(1)

    logger = setup_logging()
    config = load_config()
    env = build_env(config)

    if not check_claude_available(env):
        logger.error(
            "Claude CLI unavailable or not logged in. "
            "Run 'claude -v' manually to diagnose."
        )
        sys.exit(1)

    if args.once:
        ok = send_ping(config, logger)
        if ok:
            save_timestamp()
        else:
            send_webhook_alert(config.get("webhook_url", ""), "Claude ping failed", logger)
        sys.exit(0 if ok else 1)

    last_ping = load_timestamp() if args.resume else None
    logger.info(
        f"Daemon started. Daily reset at "
        f"{args.daily_reset[0]:02d}:{args.daily_reset[1]:02d}. "
        f"Interval: {PING_INTERVAL}."
    )

    ping_count = 0
    while True:
        next_ping = calculate_next_ping(last_ping, args.daily_reset)

        # Reset counter at each daily anchor ping; otherwise increment
        reset_h, reset_m = args.daily_reset
        if next_ping.hour == reset_h and next_ping.minute == reset_m:
            ping_count = 1
        else:
            ping_count = min(ping_count + 1, 4)
        buffer = PING_BUFFER_BASE * ping_count  # 10s, 20s, 30s, 40s

        wait_sec = (next_ping - datetime.now()).total_seconds()
        if wait_sec > 0:
            logger.info(
                f"Next ping at {next_ping.strftime('%Y-%m-%d %H:%M:%S')} "
                f"(ping #{ping_count}, sleeping {wait_sec:.0f}s + {buffer}s buffer)"
            )
            time.sleep(wait_sec)

        time.sleep(buffer)
        ok = send_ping(config, logger)
        if ok:
            last_ping = next_ping  # record scheduled target, not actual time
            save_timestamp(last_ping)
        else:
            send_webhook_alert(
                config.get("webhook_url", ""), "Claude ping failed", logger
            )
            logger.warning("Ping failed. Will retry at next scheduled time.")


if __name__ == "__main__":
    main()
