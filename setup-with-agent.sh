#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
AGENT=${1:-all}

open_url() {
  label=$1
  url=$2
  printf '\n%s\n%s\n' "下一步：$label" "$url"
  if command -v open >/dev/null 2>&1; then
    open "$url"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 &
  fi
  printf '%s' "请在浏览器完成这一步，完成后按回车继续："
  read -r answer
}

cd "$ROOT"
printf '%s\n' "1/7 安装 fomo-signal-tracker Skill（目标：$AGENT）"
./install-agent-skill.sh "$AGENT"

printf '%s\n' "2/7 安装并启动本机 Fomo Monitor 服务"
python3 monitor/install.py

open_url "3/7 使用 David小鱼的邀请码注册或登录 Fomo" "https://fomo.family/r/PurePastMacaw"
open_url "4/7 登录 GMGN，用于多链热搜筛选" "https://gmgn.ai/"
open_url "5/7 登录 Windvane，用于按完整 CA 核验 Fomo 持仓" "https://wind.jokkimon.club/windvane"
open_url "6/7 配置飞书自定义机器人" "https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot"
open_url "7/7 在 Telegram 通过 BotFather 创建机器人" "https://t.me/BotFather"

if command -v open >/dev/null 2>&1; then
  open "http://127.0.0.1:8765/#connect"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://127.0.0.1:8765/#connect" >/dev/null 2>&1 &
fi

printf '%s\n' "已打开 Fomo Monitor 的消息连接页。请粘贴飞书 Webhook 和 Telegram Bot Token，并测试推送。"
