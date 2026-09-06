#!/bin/zsh
launchctl kickstart gui/$(id -u)/club.local.fomo-monitor 2>/dev/null
open 'http://127.0.0.1:8765/'
