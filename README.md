# Claude Session Anchor

Pin Claude Code's 5-hour usage window to a fixed daily time on your server.

## Background

Claude Code's usage limit is calculated on a 5-hour sliding window, starting from the exact minute you send the first message. If no messages are sent after the window expires, the timer pauses — it does not auto-renew.

This script sends a ping at a specified daily time to anchor the window to a predictable slot (e.g., 09:00–14:00), then renews every 5 hours to keep the window continuous throughout your working hours.

> **Note (2026-06-15 billing change):** non-interactive `claude -p` calls no longer count toward subscription usage and **stop triggering the 5-hour window** (they bill the separate Agent SDK credit pool). The ping therefore drives a **real interactive TUI session over a pseudo-terminal (PTY)** instead. The window starts at the exact moment the message is sent, so each ping **pre-warms** the TUI ahead of time and fires the message precisely at the anchor instant — cold-start jitter is absorbed before the anchor, leaving only irreducible network latency. Success is confirmed by detecting the model's reply (logged to `claude_anchor.log`).

**Example (daily-reset=09:00):**

```
09:00 → ping → window 09:00–14:00
14:00 → ping → window 14:00–19:00
19:00 → ping → window 19:00–00:00
00:00 → ping → window 00:00–05:00
         ↑ 00:00+5h=05:00, but the 05:00 window would end at 10:00, and 10:00 > next day's 09:00
         → skip 05:00, wait for tomorrow's 09:00 anchor instead
```

At most 4 pings per day. A follow-up ping is only scheduled if the window it opens would fully close before the next daily reset — this prevents a new window from overlapping the next day's anchor.

**Actual send times** are slightly after the target to absorb clock precision errors: ping #1 +10s, #2 +20s, #3 +30s, #4 +40s. The counter resets to +10s each day. Each ping spawns the TUI `preboot_lead` seconds early (default 120s) and only sends the message at the buffered target instant, so the window start stays aligned regardless of how long the TUI takes to boot.

## Requirements

- Ubuntu 22.04, Python 3.10+
- Claude Code CLI installed (`npm install -g @anthropic-ai/claude-code`)
- Interactive login completed (see Prerequisites below)

## Prerequisites (one-time setup)

```bash
# 1. Log in interactively and trust the home directory
cd ~
claude   # follow the prompts to log in, send any message, then Ctrl+C

# 2. Verify an interactive session works (this is what the anchor now drives)
claude --model haiku   # type a message, confirm a reply, then /exit
```

## Installation

```bash
git clone <repo-url> ~/claude_timer
cd ~/claude_timer
```

No additional dependencies required — standard library only.

## Configuration

Edit `config.json`:

```json
{
  "http_proxy": "http://127.0.0.1:7890",
  "https_proxy": "http://127.0.0.1:7890",
  "no_proxy": "localhost,127.0.0.1",
  "webhook_url": "",
  "timing": {
    "preboot_lead": 120,
    "quiet_period": 2,
    "response_timeout": 60,
    "reply_min_chars": 10,
    "exit_wait": 5
  }
}
```

| Field | Description |
|---|---|
| `http_proxy` / `https_proxy` | Proxy address. Leave empty to disable. Node.js natively supports HTTP proxies. |
| `no_proxy` | Comma-separated list of addresses that bypass the proxy. |
| `webhook_url` | Webhook URL for failure alerts (Slack, Feishu, etc.). Leave empty to disable. |

> **Note:** Proxy settings are explicitly injected into the subprocess environment from `config.json`, not inherited from the shell — so they work correctly under `nohup` or systemd.

### Timing (optional)

The `timing` block tunes the interactive PTY session. The whole block — and any individual key — is optional; missing values fall back to the defaults shown above.

| Key | Default | Description |
|---|---|---|
| `preboot_lead` | 120 | Seconds to spawn the TUI **before** the target send time, so cold-start jitter is absorbed before the window anchor. |
| `quiet_period` | 6 | Seconds of no output that mark the stream as settled. Must exceed the model's "thinking" pause, or detection fires before the reply lands. |
| `response_timeout` | 60 | Max seconds to wait for the model's reply after the message is sent. |
| `reply_min_chars` | 10 | Minimum visible reply characters (after the message) required to count as a successful reply. |
| `exit_wait` | 5 | Seconds to wait after `/exit` before escalating to SIGTERM, then SIGKILL. |

## Usage

### Daemon mode (recommended)

```bash
python claude_anchor.py --daily-reset=09:00
```

Sends the first ping at 09:00 every day, then renews every 5 hours.

### Send one ping and exit

```bash
python claude_anchor.py --once
```

### Resume from the last saved timestamp (after a restart)

```bash
python claude_anchor.py --daily-reset=09:00 --resume
```

Reads `last_run_timestamp.txt` and calculates the next ping based on the last recorded time, instead of waiting for the next daily reset.

## Running as a systemd service

```bash
# 1. Copy and edit the service file
cp claude_anchor.service /etc/systemd/system/
vim /etc/systemd/system/claude_anchor.service
# Update User, WorkingDirectory, and ExecStart with your paths and preferred time

# 2. Enable and start
sudo systemctl daemon-reload
sudo systemctl enable claude_anchor
sudo systemctl start claude_anchor

# 3. View logs
journalctl -u claude_anchor -f
```

`claude_anchor.service` template:

```ini
[Unit]
Description=Claude Session Anchor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/path/to/claude_timer
ExecStart=/usr/bin/python3 /path/to/claude_timer/claude_anchor.py --daily-reset=09:00
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

## File reference

| File | Description |
|---|---|
| `claude_anchor.py` | Main script |
| `config.json` | Proxy and webhook configuration |
| `claude_anchor.service` | systemd service template |
| `last_run_timestamp.txt` | Last ping time (auto-generated, used by `--resume`) |
| `claude_anchor.log` | Rotating log — max 5 MB per file, 3 files retained |

## Running tests

```bash
python -m pytest tests/ -v
```
