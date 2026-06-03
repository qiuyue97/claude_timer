# Claude Code Ping 脚本改造说明

## 背景

Anthropic 在 2026 年 6 月 15 日对订阅计划做了调整：

- **`claude -p` 和 Claude Agent SDK 的所有调用不再计入订阅用量**，改为走独立的 Agent SDK monthly credit（Pro 用户 $20/月，按 API 标准价扣费，月底不滚存）
- **交互式 Claude Code（TUI 模式）、Claude Cowork、claude.ai 网页/桌面/移动端聊天**仍然走订阅用量
- 订阅的 5 小时滚动窗口由"第一个计入订阅用量的请求"触发

官方说明：https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan

## 当前脚本（6 月 15 日后将失效）

我现在在 cron 中跑的 ping 脚本，用于锚定 5 小时窗口的起始时间到 6:00 AM：

```bash
claude -p "Hi" --model haiku --no-session-persistence
```

## 失效原因

`-p` 标记表示 print/非交互模式，6 月 15 日后这类调用全部划入 Agent SDK credit 池，**不再触发 5 小时窗口**。脚本继续跑只会扣 $20 credit 的钱，但起不到锚定订阅窗口的作用。

## 改造目标

把 ping 脚本改为**交互式调用**，使其计入订阅用量并触发 5 小时窗口。脚本必须能在 cron 中无人值守运行。

## 推荐方案：用 `expect` 驱动 TUI 会话

> **实现说明（2026-06-03）**：本项目最终未使用独立 `expect` 脚本，而是用**纯 Python `pty`** 在 `claude_anchor.py` 内驱动交互式 TUI（零额外依赖、跨平台可测）。在此基础上增加了**预热 + 到点发**：提前 `preboot_lead` 秒 spawn TUI，到精确的锚点时刻才发送消息，以消除冷启动抖动、保证 5 小时窗口起点精度；成功判定改为"检测到模型回复"。设计/计划见 `docs/superpowers/specs/` 与 `docs/superpowers/plans/`。

把现有 cron 任务替换成下面的 expect 脚本。原理：模拟真实的交互式 TUI 会话，发一条消息后 `/exit` 退出。

```bash
#!/usr/bin/expect -f
# warmup.sh - 锚定 Claude Code 5 小时窗口
# 用法：放在 cron 中定时执行
# 0 6 * * 1-5 /path/to/warmup.sh >/dev/null 2>&1

set timeout 30

spawn claude --model haiku --no-session-persistence

# 等待 TUI 启动并出现输入提示
expect -re "(>|❯|>>|\\?)"

# 发送一条最小化消息
send "ping\r"

# 等待响应完成
expect -re "(>|❯|>>|\\?)"

# 退出
send "/exit\r"
expect eof
```

## 部署步骤

1. 把脚本保存为 `~/scripts/claude-warmup.sh` 并 `chmod +x`
2. 确认环境已安装 expect：`which expect` 或 `apt install expect`（Ubuntu）
3. 确认 cron 环境能正确加载我的 Claude Code 配置（PATH、HOME、登录凭证），如必要在脚本顶部 `source ~/.profile` 或显式设置环境变量
4. 替换原 cron 行为新脚本
5. 6 月 15 日当天或之后第一天**先手动跑一次**，立即检查窗口是否被锚定（见下方验证方法）

## 验证方法

跑完脚本后立即用以下任一方式确认 5 小时窗口已启动：

- **`ccusage`**：查看当前窗口剩余 token 和重置时间，确认窗口起点是脚本执行后的整点
- **Claude Code 交互式会话内 `/status`**：查看 session 信息
- **观察实际工作时段**：5 小时后窗口确实如期重置，且整个窗口期间一直 align 你的工作时间

如果窗口**没有**被锚定（即工作时第一条 prompt 才触发窗口），说明 expect 方案也被 Anthropic 判为非交互调用了，走方案 B。

## 方案 B（备选，如果 expect 失效）

Anthropic 有可能用 TTY 检测、调用上下文等更细的方式判定交互/非交互。如果 expect 不行，只剩手动模式：

- iOS/Android 上的 Claude App 早上发一条 "hi" 即可
- 可以做成 iOS 快捷指令 + 自动化定时触发，每天早上 6 点自动发送
- 这条路 Anthropic 不太可能堵，因为它本质就是真人发消息

## 注意事项

- **不要**在脚本里用 `claude -p`，否则又掉回 Agent SDK credit 池子
- **不要**用 Claude Code GitHub Actions 来做这件事——官方公告里它也被划入 Agent SDK credit
- 这个脚本本身消耗很少 token（一次 Haiku 的最短调用），对周限额几乎无影响
- 周限额（weekly cap）这个机制本次没变，仍然独立存在
- 6 月 15 日是关键节点，**在那之前**这个改造可以不做；**那之后**原脚本就废了

## 给 Claude Code 的指令

请帮我：

1. 检查当前 cron 配置（`crontab -l`），找到现有的 `claude -p` ping 脚本
2. 按上面"推荐方案"创建新的 expect 脚本到 `~/scripts/claude-warmup.sh`
3. 更新 crontab，把旧的 ping 行替换为调用新脚本
4. 验证 expect 已安装；如未安装，提示我执行安装命令
5. 帮我准备一个一次性手动测试命令，方便我在 6 月 15 日之后立即验证
