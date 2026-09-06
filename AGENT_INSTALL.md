# Agent Skill 安装

仓库内的 [`skills/fomo-signal-tracker`](skills/fomo-signal-tracker) 是自动监测使用的完整 Skill，包含主规则、跟踪字段、经济模型说明和独立策略计算脚本。

## 一键安装

macOS / Linux 在项目根目录运行：

```bash
chmod +x install-agent-skill.sh
./install-agent-skill.sh all
```

脚本会安装到：

| Agent | 目标目录 | 单独安装命令 |
|---|---|---|
| Codex | `~/.codex/skills/fomo-signal-tracker` | `./install-agent-skill.sh codex` |
| Claude Code | `~/.claude/skills/fomo-signal-tracker` | `./install-agent-skill.sh claude` |
| WorkBuddy | `~/.workbuddy/skills/fomo-signal-tracker` | `./install-agent-skill.sh workbuddy` |
| 通用 Agents | `~/.agents/skills/fomo-signal-tracker` | `./install-agent-skill.sh agents` |

更新时再次运行即可。安装器先把旧目录改名为带时间戳的备份，再写入新版本。

## 手动安装

将整个 `skills/fomo-signal-tracker` 文件夹复制到对应 Agent 的 Skills 目录。必须保留 `references/`、`scripts/` 和 `agents/`，不能只复制 `SKILL.md`。

WorkBuddy 的本机推荐市场安装器说明其默认 Skill 目录为 `~/.workbuddy/skills/`。安装完成后重启 WorkBuddy；如果当前版本只支持界面导入，可在技能管理中选择仓库里的 `skills/fomo-signal-tracker/SKILL.md`。

## 调用方式

新建会话后输入：

```text
使用 fomo-signal-tracker，扫描 GMGN 多链热搜，通过 Windvane 核验，按洗盘回升规则确认模拟买点，并维护100U回本滚仓账本。
```

Skill 负责规则和执行流程。本机网页、SQLite、飞书与 Telegram 推送还需要安装项目服务：

```bash
python3 monitor/install.py
```

定时运行需要在所用 Agent 中额外建立周期任务。Codex 用户可直接要求：“每5分钟按照 fomo-signal-tracker 执行监测，状态无变化时保持静默”。
