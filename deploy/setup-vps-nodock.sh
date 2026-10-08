#!/usr/bin/env bash
# First-time setup on Contabo VPS (Ubuntu 22.04) — NO Docker
# Run as root:  bash setup-vps-nodock.sh
set -e

echo "=== Installing system packages ==="
apt-get update -y
apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3-pip \
    ffmpeg \
    git \
    nginx \
    fonts-liberation \
    logrotate \
    curl

echo "=== Cloning repo ==="
git clone https://github.com/Bardboy2/kick-automation.git /opt/kick_automation
cd /opt/kick_automation

echo "=== Creating Python venv ==="
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "=== Creating data directories ==="
mkdir -p data/clips data/vods data/state logs

echo "=== Copying config ==="
cp config/.env.example config/.env
echo ""
echo ">>> Edit your API keys: nano /opt/kick_automation/config/.env <<<"
echo ""

echo "=== Installing systemd services ==="
cp deploy/kick-bot.service    /etc/systemd/system/
cp deploy/kick-editor.service /etc/systemd/system/
systemctl daemon-reload

echo "=== Setting up nginx ==="
cp deploy/nginx-nodock.conf /etc/nginx/sites-available/kick
ln -sf /etc/nginx/sites-available/kick /etc/nginx/sites-enabled/kick
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "=== Setting up log rotation ==="
cp deploy/logrotate.conf /etc/logrotate.d/kick_automation

echo "=== Setting up clip cleanup cron ==="
echo "0 3 * * * root bash /opt/kick_automation/deploy/cleanup-clips.sh" \
    > /etc/cron.d/kick_cleanup

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. nano /opt/kick_automation/config/.env   (fill in your keys)"
echo "  2. bash /opt/kick_automation/deploy/start-nodock.sh"
