#!/usr/bin/env bash
# Safe install for shared VPS — installs ONLY under /opt/kick_automation
# Constraints: do NOT touch Node.js, do NOT write to /root/whatsapp-bots/
set -e

echo "=== Checking Python version ==="
PY=$(python3 --version 2>&1)
echo "Found: $PY"
# faster-whisper requires 3.8+; warn if below 3.10
python3 -c "import sys; assert sys.version_info >= (3,8), 'Need Python 3.8+'" \
    || { echo "ERROR: Python 3.8+ required"; exit 1; }

echo "=== Installing only missing system packages ==="
# ffmpeg for video processing, nginx for caption editor proxy
# --no-upgrade so we never touch held packages (Node etc.)
apt-get update -y -q
apt-get install -y --no-upgrade --no-install-recommends \
    ffmpeg \
    nginx \
    fonts-liberation \
    logrotate

echo "=== Cloning repo into /opt/kick_automation ==="
if [ -d /opt/kick_automation ]; then
    echo "Directory exists — pulling latest..."
    git -C /opt/kick_automation pull
else
    git clone https://github.com/Bardboy2/kick-automation.git /opt/kick_automation
fi
cd /opt/kick_automation

echo "=== Creating Python venv (self-contained under /opt/kick_automation) ==="
python3 -m venv .venv
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements.txt --quiet

echo "=== Creating data directories ==="
mkdir -p data/clips data/vods data/state logs

echo "=== Copying config ==="
if [ ! -f config/.env ]; then
    cp config/.env.example config/.env
    echo ""
    echo ">>> Fill in your API keys: nano /opt/kick_automation/config/.env <<<"
fi

echo "=== Installing systemd services ==="
cp deploy/kick-bot.service    /etc/systemd/system/
cp deploy/kick-editor.service /etc/systemd/system/
systemctl daemon-reload

echo "=== Configuring nginx ==="
cp deploy/nginx-nodock.conf /etc/nginx/sites-available/kick
ln -sf /etc/nginx/sites-available/kick /etc/nginx/sites-enabled/kick
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "=== Setting up log rotation ==="
cp deploy/logrotate.conf /etc/logrotate.d/kick_automation

echo "=== Setting up 48h clip cleanup cron ==="
echo "0 * * * * root bash /opt/kick_automation/deploy/cleanup-clips.sh >> /opt/kick_automation/logs/cleanup.log 2>&1" \
    > /etc/cron.d/kick_cleanup

echo ""
echo "✅ Setup complete! Next:"
echo "  1. nano /opt/kick_automation/config/.env   ← add your API keys"
echo "  2. bash /opt/kick_automation/deploy/start-nodock.sh"
