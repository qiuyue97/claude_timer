import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections import namedtuple
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


# --- interactive-session output parsing ---

_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")  # OSC ... BEL/ST
_ANSI_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")          # CSI ... final byte
_ANSI_ESC = re.compile(r"\x1b[@-Z\\-_]")                       # other single escapes
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")        # control chars, keep \t\n\r

ERROR_MARKERS = ("Invalid API key", "Please run /login", "API Error", "not logged in")


def strip_ansi(data):
    data = _ANSI_OSC.sub("", data)
    data = _ANSI_CSI.sub("", data)
    data = _ANSI_ESC.sub("", data)
    data = _CTRL.sub("", data)
    return data


def visible_char_count(data):
    return len(re.sub(r"\s+", "", strip_ansi(data)))


def find_error_marker(data):
    low = strip_ansi(data).lower()
    for marker in ERROR_MARKERS:
        if marker.lower() in low:
            return marker
    return None


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

# --- interactive PTY ping ---
PTY_COLS = 120
PTY_ROWS = 40
PTY_TERM = "xterm-256color"
PING_MESSAGE = "ping"
# NOTE: --no-session-persistence is print-mode (-p) only; the interactive TUI
# rejects it and exits immediately. Interactive sessions persist by default.
PING_ARGV = ["claude", "--model", "haiku"]
ONCE_BOOT_CAP = 30  # --once: fire after readiness, but no later than this many seconds

PtyResult = namedtuple(
    "PtyResult", ["reply_seen", "reply_text", "exited_early", "error_marker", "raw_tail"]
)


def compute_fire_at(next_ping, buffer_seconds):
    return next_ping + timedelta(seconds=buffer_seconds)


def compute_wake_at(fire_at, preboot_lead):
    return fire_at - timedelta(seconds=preboot_lead)


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


def send_ping(config, logger, *, fire_at=None):
    env = build_env(config)
    timing = config.get("timing", DEFAULT_CONFIG["timing"])
    if fire_at is not None:
        logger.info(f"Pre-warming Claude TUI; firing at {fire_at.strftime('%H:%M:%S')}...")
    else:
        logger.info("Sending interactive ping to Claude...")

    result = _drive_pty_session(PING_ARGV, env, timing, fire_at=fire_at, logger=logger)

    if result.exited_early:
        logger.error(f"Claude exited before the message was sent. Output tail:\n{result.raw_tail}")
        return False
    if result.error_marker:
        logger.error(f"Ping failed: detected '{result.error_marker}'. Output tail:\n{result.raw_tail}")
        return False
    if not result.reply_seen:
        logger.error(f"No reply detected within timeout. Output tail:\n{result.raw_tail}")
        return False
    logger.info(f"Ping successful. Reply:\n{result.reply_text[:500]}")
    return True


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


def _drive_pty_session(argv, env, timing, fire_at, logger):
    """Drive an interactive TUI session over a real PTY.

    fire_at is None       -> once mode: fire as soon as the TUI looks ready (capped).
    fire_at is a datetime -> precise mode: pre-warm, then fire exactly at fire_at.
    Returns a PtyResult. Does not raise on child errors. POSIX only.
    """
    if os.name != "posix":
        raise RuntimeError("PTY ping requires a POSIX system")
    import pty
    import select
    import struct
    import fcntl
    import termios

    quiet_period = float(timing.get("quiet_period", 2))
    response_timeout = float(timing.get("response_timeout", 60))
    reply_min_chars = int(timing.get("reply_min_chars", 10))
    exit_wait = float(timing.get("exit_wait", 5))

    master_fd, slave_fd = pty.openpty()
    proc = None
    raw = []        # all decoded output
    post = []       # output accumulated after the message is sent
    fired = False

    def read_once(window):
        """Read available bytes for up to `window` seconds. Returns (got_bytes, eof)."""
        r, _, _ = select.select([master_fd], [], [], window)
        if master_fd not in r:
            return False, False
        try:
            data = os.read(master_fd, 65536)
        except OSError:
            return False, True
        if not data:
            return False, True
        chunk = data.decode("utf-8", "replace")
        raw.append(chunk)
        if fired:
            post.append(chunk)
        return True, False

    def raw_tail():
        return "\n".join(strip_ansi("".join(raw)).splitlines()[-20:])

    try:
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", PTY_ROWS, PTY_COLS, 0, 0))
        child_env = dict(env)
        child_env["TERM"] = PTY_TERM
        proc = subprocess.Popen(
            argv, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            env=child_env, start_new_session=True, close_fds=True,
            cwd=str(Path(__file__).parent),  # always launch in the (trusted) project folder
        )
        os.close(slave_fd)
        slave_fd = -1

        # ---- pre-warm / readiness phase (until fire_at, or until ready in once mode) ----
        last_activity = time.monotonic()
        boot_deadline = time.monotonic() + ONCE_BOOT_CAP  # only used in once mode
        while True:
            if proc.poll() is not None:
                while True:  # drain anything still buffered
                    got, eof = read_once(0.1)
                    if eof or not got:
                        break
                return PtyResult(False, "", True, find_error_marker("".join(raw)), raw_tail())

            got, eof = read_once(0.2)
            if got:
                last_activity = time.monotonic()
            if eof:
                return PtyResult(False, "", True, find_error_marker("".join(raw)), raw_tail())

            ready = (len(raw) > 0) and (time.monotonic() - last_activity >= quiet_period)
            if fire_at is None:
                if ready or time.monotonic() >= boot_deadline:
                    break
            else:
                if datetime.now() >= fire_at:
                    break

        # ---- fire the message ----
        os.write(master_fd, (PING_MESSAGE + "\r").encode())
        fired = True
        logger.info("Message sent; waiting for reply...")

        # ---- reply detection phase ----
        reply_deadline = time.monotonic() + response_timeout
        last_activity = time.monotonic()
        reply_seen = False
        while time.monotonic() < reply_deadline:
            if proc.poll() is not None:
                break
            got, eof = read_once(0.2)
            if got:
                last_activity = time.monotonic()
            if eof:
                break
            enough = visible_char_count("".join(post)) >= reply_min_chars
            quiesced = time.monotonic() - last_activity >= quiet_period
            if enough and quiesced:
                reply_seen = True
                break
        if not reply_seen:
            reply_seen = visible_char_count("".join(post)) >= reply_min_chars

        # ---- graceful exit ----
        if proc.poll() is None:
            os.write(master_fd, b"/exit\r")
            end = time.monotonic() + exit_wait
            while time.monotonic() < end and proc.poll() is None:
                read_once(0.2)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)

        reply_text = strip_ansi("".join(post)).strip()
        return PtyResult(reply_seen, reply_text, False, find_error_marker("".join(raw)), raw_tail())
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        if slave_fd != -1:
            try:
                os.close(slave_fd)
            except OSError:
                pass
        if proc is not None and proc.poll() is None:
            proc.kill()


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

        fire_at = compute_fire_at(next_ping, buffer)
        wake_at = compute_wake_at(fire_at, config["timing"]["preboot_lead"])

        # Wake PREBOOT_LEAD seconds early so cold-start jitter is absorbed before
        # the anchor instant. The driver then waits internally until fire_at.
        wait_sec = (wake_at - datetime.now()).total_seconds()
        if wait_sec > 0:
            logger.info(
                f"Next ping at {fire_at.strftime('%Y-%m-%d %H:%M:%S')} "
                f"(ping #{ping_count}, +{buffer}s buffer); pre-warming at "
                f"{wake_at.strftime('%H:%M:%S')} (sleeping {wait_sec:.0f}s)"
            )
            time.sleep(wait_sec)

        ok = send_ping(config, logger, fire_at=fire_at)
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
