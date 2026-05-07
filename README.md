# Claude Session Anchor

Pin Claude Code's 5-hour usage window to a fixed daily time on your server.

## Background

Claude Code's usage limit is calculated on a 5-hour sliding window, starting from the exact minute you send the first message. If no messages are sent after the window expires, the timer pauses — it does not auto-renew.

This script sends a ping at a specified daily time to anchor the window to a predictable slot (e.g., 09:00–14:00), then renews every 5 hours to keep the window continuous throughout your working hours.

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

**Actual send times** are slightly after the target to absorb clock precision errors: ping #1 +10s, #2 +20s, #3 +30s, #4 +40s. The counter resets to +10s each day.

## Requirements

- Ubuntu 22.04, Python 3.10+
- Claude Code CLI installed (`npm install -g @anthropic-ai/claude-code`)
- Interactive login completed (see Prerequisites below)

## Prerequisites (one-time setup)

```bash
# 1. Log in interactively and trust the home directory
cd ~
claude   # follow the prompts to log in, send any message, then Ctrl+C

# 2. Verify non-interactive mode works
claude -p "Hi" --model haiku --no-session-persistence
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
  "webhook_url": ""
}
```

| Field | Description |
|---|---|
| `http_proxy` / `https_proxy` | Proxy address. Leave empty to disable. Node.js natively supports HTTP proxies. |
| `no_proxy` | Comma-separated list of addresses that bypass the proxy. |
| `webhook_url` | Webhook URL for failure alerts (Slack, Feishu, etc.). Leave empty to disable. |

> **Note:** Proxy settings are explicitly injected into the subprocess environment from `config.json`, not inherited from the shell — so they work correctly under `nohup` or systemd.

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
