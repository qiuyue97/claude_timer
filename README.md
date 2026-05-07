# Claude Session Anchor

在服务器上将 Claude Code 的 5 小时用量窗口锁定到每天固定时间。

## 背景

Claude Code 的用量限制按 5 小时滑动窗口计算，窗口从你发第一条消息的那一分钟开始计时。如果窗口到期后没有新消息，计时暂停，不会自动续期。

这个脚本在你每天指定的时间发一次 ping，把窗口锚定到一个可预期的时间点（如 09:00–14:00），之后每隔整整 5 小时续一次，让窗口在工作时段保持连续。

**示例（daily-reset=09:00）：**

```
09:00 → ping → 窗口 09:00–14:00
14:00 → ping → 窗口 14:00–19:00
19:00 → ping → 窗口 19:00–00:00
00:00 → ping → 窗口 00:00–05:00
05:00 → ping → 窗口 05:00–10:00  ← 超过次日 09:00，改为等到 09:00
次日 09:00 → ping → 重新锚定
```

## 环境要求

- Ubuntu 22.04，Python 3.10+
- Claude Code CLI 已安装（`npm install -g @anthropic-ai/claude-code`）
- 已完成交互式登录（见下方前置步骤）

## 前置步骤（一次性）

```bash
# 1. 交互式登录并信任 home 目录
cd ~
claude   # 按提示登录，随便发一条消息，Ctrl+C 退出

# 2. 验证非交互模式可用
claude -p "Hi" --model haiku --no-session-persistence
```

## 安装

```bash
git clone <repo-url> ~/claude_timer
cd ~/claude_timer
```

无需安装额外依赖，只用标准库。

## 配置

编辑 `config.json`：

```json
{
  "http_proxy": "http://127.0.0.1:7890",
  "https_proxy": "http://127.0.0.1:7890",
  "no_proxy": "localhost,127.0.0.1",
  "webhook_url": ""
}
```

| 字段 | 说明 |
|---|---|
| `http_proxy` / `https_proxy` | 代理地址，留空则不注入。Node.js 原生支持 HTTP 代理 |
| `no_proxy` | 不走代理的地址列表 |
| `webhook_url` | 失败告警的 webhook 地址（Slack/飞书等），留空则不发送 |

> **注意：** 代理从 `config.json` 显式注入子进程环境，不依赖 `.bashrc`，在 `nohup`/systemd 启动时也能正常生效。

## 用法

### 常驻守护进程（推荐）

```bash
python claude_anchor.py --daily-reset=09:00
```

每天 09:00 发第一次 ping，之后每 5 小时续一次。

### 立即发一次 ping 后退出

```bash
python claude_anchor.py --once
```

### 从上次记录的时间戳恢复（重启后）

```bash
python claude_anchor.py --daily-reset=09:00 --resume
```

读取 `last_run_timestamp.txt`，基于上次 ping 时间推算下次，而不是等到次日 reset。

## 以 systemd 服务运行

```bash
# 1. 复制并编辑服务文件
cp claude_anchor.service /etc/systemd/system/
vim /etc/systemd/system/claude_anchor.service
# 修改 User、WorkingDirectory、ExecStart 中的路径和时间

# 2. 启用并启动
sudo systemctl daemon-reload
sudo systemctl enable claude_anchor
sudo systemctl start claude_anchor

# 3. 查看日志
journalctl -u claude_anchor -f
```

`claude_anchor.service` 模板：

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

## 文件说明

| 文件 | 说明 |
|---|---|
| `claude_anchor.py` | 主脚本 |
| `config.json` | 代理和 webhook 配置 |
| `claude_anchor.service` | systemd 服务模板 |
| `last_run_timestamp.txt` | 上次 ping 时间（自动生成，供 `--resume` 使用） |
| `claude_anchor.log` | 滚动日志，单文件最大 5MB，保留 3 份 |

## 运行测试

```bash
python -m pytest tests/ -v
```
