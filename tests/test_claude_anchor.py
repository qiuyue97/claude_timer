import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from claude_anchor import (
    load_config, build_env, DEFAULT_CONFIG,
    save_timestamp, load_timestamp,
    calculate_next_ping, PING_INTERVAL,
    check_claude_available, send_ping, send_webhook_alert,
    parse_daily_reset,
    strip_ansi, visible_char_count, find_error_marker,
    PtyResult, compute_fire_at, compute_wake_at,
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


def test_load_config_includes_timing_defaults(tmp_path):
    result = load_config(tmp_path / "nonexistent.json")
    assert result["timing"]["preboot_lead"] == 120
    assert result["timing"]["quiet_period"] == 2
    assert result["timing"]["response_timeout"] == 60
    assert result["timing"]["reply_min_chars"] == 10
    assert result["timing"]["exit_wait"] == 5


def test_load_config_deep_merges_timing(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"timing": {"preboot_lead": 30}}))
    result = load_config(cfg_path)
    assert result["timing"]["preboot_lead"] == 30       # overridden
    assert result["timing"]["quiet_period"] == 2         # default preserved
    assert result["timing"]["exit_wait"] == 5            # default preserved


# ---------- output parsing helpers ----------

def test_strip_ansi_removes_csi_color_codes():
    assert strip_ansi("\x1b[31mhello\x1b[0m") == "hello"


def test_strip_ansi_removes_osc_and_control_chars():
    assert strip_ansi("\x1b]0;title\x07ab\x08c") == "abc"


def test_strip_ansi_keeps_newlines():
    assert strip_ansi("a\x1b[2Kb\nc") == "ab\nc"


def test_visible_char_count_ignores_whitespace_and_ansi():
    assert visible_char_count("\x1b[31ma b\tc\x1b[0m\n") == 3


def test_find_error_marker_detects_login_prompt():
    assert find_error_marker("...\nPlease run /login to continue\n") == "Please run /login"


def test_find_error_marker_is_case_insensitive():
    assert find_error_marker("ERROR: invalid api key") == "Invalid API key"


def test_find_error_marker_returns_none_when_clean():
    assert find_error_marker("Reply: everything is fine") is None


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


# ---------- Task 4: scheduling ----------

def test_next_ping_no_last_ping_before_reset():
    now = datetime(2026, 5, 7, 8, 0)
    result = calculate_next_ping(None, (9, 0), now=now)
    assert result == datetime(2026, 5, 7, 9, 0)


def test_next_ping_no_last_ping_after_reset():
    now = datetime(2026, 5, 7, 10, 0)
    result = calculate_next_ping(None, (9, 0), now=now)
    assert result == datetime(2026, 5, 8, 9, 0)


def test_next_ping_interval_within_cycle():
    last_ping = datetime(2026, 5, 7, 9, 0)
    now = datetime(2026, 5, 7, 9, 1)
    result = calculate_next_ping(last_ping, (9, 0), now=now)
    assert result == datetime(2026, 5, 7, 14, 0)


def test_next_ping_interval_before_next_reset():
    last_ping = datetime(2026, 5, 7, 14, 0)
    now = datetime(2026, 5, 7, 14, 1)
    result = calculate_next_ping(last_ping, (9, 0), now=now)
    assert result == datetime(2026, 5, 7, 19, 0)


def test_next_ping_interval_after_next_reset():
    last_ping = datetime(2026, 5, 7, 22, 0)
    now = datetime(2026, 5, 7, 22, 1)
    result = calculate_next_ping(last_ping, (9, 0), now=now)
    assert result == datetime(2026, 5, 8, 3, 0)


def test_ping_interval_is_5_hours():
    assert PING_INTERVAL == timedelta(hours=5)


def test_next_ping_skips_interval_when_window_would_overlap_reset():
    # 20:00 + 5h = 01:00, but 01:00 window ends at 06:00 > reset 05:00
    # → must skip 01:00 and go straight to next reset 05:00
    last_ping = datetime(2026, 5, 7, 20, 0)
    now = datetime(2026, 5, 7, 20, 1)
    result = calculate_next_ping(last_ping, (5, 0), now=now)
    assert result == datetime(2026, 5, 8, 5, 0)


def test_next_ping_uses_interval_when_window_fits_before_reset():
    # 15:00 + 5h = 20:00, 20:00 window ends at 01:00 < reset 05:00 next day → use 20:00
    last_ping = datetime(2026, 5, 7, 15, 0)
    now = datetime(2026, 5, 7, 15, 1)
    result = calculate_next_ping(last_ping, (5, 0), now=now)
    assert result == datetime(2026, 5, 7, 20, 0)


# ---------- time math + result type ----------

def test_compute_fire_at_adds_buffer_seconds():
    next_ping = datetime(2026, 6, 16, 9, 0, 0)
    assert compute_fire_at(next_ping, 30) == datetime(2026, 6, 16, 9, 0, 30)


def test_compute_wake_at_subtracts_preboot_lead():
    fire_at = datetime(2026, 6, 16, 9, 0, 30)
    assert compute_wake_at(fire_at, 120) == datetime(2026, 6, 16, 8, 58, 30)


def test_pty_result_fields():
    r = PtyResult(reply_seen=True, reply_text="pong", exited_early=False,
                  error_marker=None, raw_tail="...")
    assert r.reply_seen is True
    assert r.reply_text == "pong"
    assert r.exited_early is False
    assert r.error_marker is None


# ---------- Task 5: claude interaction ----------

def test_check_claude_available_returns_true_on_success():
    with patch("claude_anchor.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert check_claude_available(os.environ.copy()) is True


def test_check_claude_available_returns_false_on_nonzero():
    with patch("claude_anchor.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert check_claude_available(os.environ.copy()) is False


def test_check_claude_available_returns_false_when_not_found():
    with patch("claude_anchor.subprocess.run", side_effect=FileNotFoundError):
        assert check_claude_available(os.environ.copy()) is False


def test_send_ping_returns_true_on_success():
    config = {"http_proxy": "", "https_proxy": "", "no_proxy": "", "webhook_url": ""}
    logger = MagicMock()
    with patch("claude_anchor.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert send_ping(config, logger) is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["claude", "-p", "Hi", "--model", "haiku", "--no-session-persistence"]


def test_send_ping_returns_false_on_failure():
    config = {"http_proxy": "", "https_proxy": "", "no_proxy": "", "webhook_url": ""}
    logger = MagicMock()
    with patch("claude_anchor.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        assert send_ping(config, logger) is False


def test_send_webhook_alert_skips_empty_url():
    logger = MagicMock()
    with patch("claude_anchor.urllib.request.urlopen") as mock_open:
        send_webhook_alert("", "test message", logger)
        mock_open.assert_not_called()


def test_send_webhook_alert_posts_json():
    logger = MagicMock()
    with patch("claude_anchor.urllib.request.urlopen") as mock_open:
        send_webhook_alert("http://hooks.example.com/test", "ping failed", logger)
        mock_open.assert_called_once()


# ---------- Task 6: CLI arg parsing ----------

def test_parse_daily_reset_valid_times():
    assert parse_daily_reset("09:00") == (9, 0)
    assert parse_daily_reset("23:59") == (23, 59)
    assert parse_daily_reset("00:00") == (0, 0)


def test_parse_daily_reset_invalid_format():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_daily_reset("9am")


def test_parse_daily_reset_invalid_values():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_daily_reset("25:00")
