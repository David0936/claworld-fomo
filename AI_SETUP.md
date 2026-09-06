# 交给 AI Agent 的完整安装流程

当用户要求安装本项目时，Agent 按以下顺序执行。一次只推进一个网站；安装、打开页面和本机验证可以自动完成，账号、验证码、钱包登录、OAuth 授权和密钥填写必须由用户本人处理。

## 自动安装

```bash
git clone https://github.com/David0936/claworld-fomo.git
cd claworld-fomo
chmod +x install-agent-skill.sh setup-with-agent.sh
./setup-with-agent.sh all
```

也可将最后一个参数改为 `codex`、`claude`、`workbuddy` 或 `agents`。

## Agent 必须依次完成

1. 安装 `fomo-signal-tracker` Skill。
2. 执行 `python3 monitor/install.py`，确认 `http://127.0.0.1:8765/` 可访问。
3. 打开 [Fomo 作者邀请码](https://fomo.family/r/PurePastMacaw)，等待用户注册或登录。该链接是 David小鱼的邀请链接，使用后作者可能获得平台奖励。
4. 打开 [GMGN](https://gmgn.ai/)，等待用户完成登录；只用于行情观察，不连接自动交易。
5. 打开 [Windvane](https://wind.jokkimon.club/windvane)，等待用户完成登录并确认“查 CA”可用。
6. 打开 [飞书自定义机器人指南](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)，等待用户创建机器人并取得 Webhook。
7. 打开 [Telegram BotFather](https://t.me/BotFather)，等待用户创建 Bot 并取得 Token。
8. 打开 `http://127.0.0.1:8765/#connect`，由用户粘贴凭证，分别测试飞书和 Telegram。
9. 在 Agent 中建立周期任务：每5分钟按 `fomo-signal-tracker` 扫描；已有项目的有效趋势确认仍按两小时采样；没有新信号或状态变化时保持静默。

## 完成标准

- 对应 Agent 能发现 `fomo-signal-tracker`。
- 本机工作台可打开。
- GMGN 与 Windvane 已登录。
- 至少一个消息通道测试成功。
- 周期任务已启用，且明确只监测和模拟，不执行交易。
