#!/usr/bin/env bash
# Pull latest code and restart — run this whenever you update the scripts
set -e
cd "$(dirname "$0")/.."

git pull
docker compose build
docker compose up -d --force-recreate
echo "✅ Updated and restarted."
