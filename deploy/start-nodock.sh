#!/usr/bin/env bash
# Start / restart bot + editor
set -e
systemctl enable kick-bot kick-editor
systemctl restart kick-bot kick-editor

echo "✅ Services started!"
echo ""
systemctl status kick-bot --no-pager -l
echo ""
systemctl status kick-editor --no-pager -l
echo ""
echo "Caption editor: http://$(curl -s ifconfig.me 2>/dev/null || echo YOUR_VPS_IP)"
echo ""
echo "Live bot logs:    journalctl -u kick-bot -f"
echo "Live editor logs: journalctl -u kick-editor -f"
