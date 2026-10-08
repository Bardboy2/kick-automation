#!/bin/bash
# Watchdog: keeps telegram_bot.py running forever
PYTHON=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
BOT=/Users/user/Downloads/kick_automation/scripts/telegram_bot.py
LOG=/Users/user/Downloads/kick_automation/logs/bot.log
cd /Users/user/Downloads/kick_automation

while true; do
    echo "$(date '+%Y-%m-%dT%H:%M:%S') [watchdog] Starting bot..." >> "$LOG"
    "$PYTHON" "$BOT" >> "$LOG" 2>&1
    echo "$(date '+%Y-%m-%dT%H:%M:%S') [watchdog] Bot exited — restarting in 5s..." >> "$LOG"
    sleep 5
done
