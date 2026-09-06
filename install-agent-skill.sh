#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE="$ROOT/skills/fomo-signal-tracker"
TARGET=${1:-all}

install_skill() {
  base=$1
  label=$2
  mkdir -p "$base"
  destination="$base/fomo-signal-tracker"
  staging="$base/.fomo-signal-tracker.tmp.$$"
  trap 'rm -rf "$staging"' EXIT INT TERM
  cp -R "$SOURCE" "$staging"
  if [ -e "$destination" ]; then
    backup="$destination.backup.$(date +%Y%m%d%H%M%S)"
    mv "$destination" "$backup"
    printf '%s\n' "${label}：旧版本已备份到 $backup"
  fi
  mv "$staging" "$destination"
  trap - EXIT INT TERM
  printf '%s\n' "${label}：已安装到 $destination"
}

case "$TARGET" in
  codex) install_skill "$HOME/.codex/skills" "Codex" ;;
  claude) install_skill "$HOME/.claude/skills" "Claude Code" ;;
  workbuddy) install_skill "$HOME/.workbuddy/skills" "WorkBuddy" ;;
  agents) install_skill "$HOME/.agents/skills" "通用 Agents" ;;
  all)
    install_skill "$HOME/.codex/skills" "Codex"
    install_skill "$HOME/.claude/skills" "Claude Code"
    install_skill "$HOME/.workbuddy/skills" "WorkBuddy"
    install_skill "$HOME/.agents/skills" "通用 Agents"
    ;;
  *)
    printf '%s\n' "用法：./install-agent-skill.sh [all|codex|claude|workbuddy|agents]" >&2
    exit 2
    ;;
esac

printf '%s\n' "安装完成。重启对应 Agent 或新建会话后，输入：使用 fomo-signal-tracker 监测 Fomo 信号"
