# 自动化接入

将 `fomo-monitor-prompt.md` 作为 Codex 周期任务提示词，建议每5分钟请求一次。任务执行时间、登录状态和账户配额会影响实际间隔；已有标的的退出确认仍必须间隔至少两小时。

自动化必须在本项目目录运行，并能调用：

```bash
python3 monitor/bridge.py event /绝对路径/事件.json
python3 monitor/bridge.py baseline /绝对路径/基线.json
python3 monitor/bridge.py sample /绝对路径/样本.json
python3 monitor/bridge.py scan complete "本轮说明"
```

项目不提交具体任务 ID、账号、Cookie、飞书或 Telegram 密钥。安装者在 Codex 中创建周期任务后，将本机工作台 `server.py` 的刷新目标配置为自己的任务 ID。
