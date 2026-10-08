#!/usr/bin/env bash
# Run this ONCE on a fresh Contabo VPS (Ubuntu 22.04)
# Usage:  bash setup-vps.sh
set -e

echo "=== Installing Docker ==="
apt-get update -y
apt-get install -y ca-certificates curl gnupg git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

echo "=== Cloning project ==="
# Change REPO_URL to your git repo (GitHub, GitLab, etc.)
REPO_URL="${REPO_URL:-https://github.com/YOUR_USERNAME/kick-automation.git}"
git clone "$REPO_URL" /opt/kick_automation
cd /opt/kick_automation

echo "=== Setting up config ==="
cp config/.env.example config/.env
echo ""
echo ">>> EDIT /opt/kick_automation/config/.env with your API keys before continuing <<<"
echo "    nano /opt/kick_automation/config/.env"
echo ""
echo "Then run:  cd /opt/kick_automation && bash deploy/start.sh"
