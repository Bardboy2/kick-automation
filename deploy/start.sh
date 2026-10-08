#!/usr/bin/env bash
# Start or restart the full stack
set -e
cd "$(dirname "$0")/.."

echo "=== Building image ==="
docker compose build

echo "=== Starting services ==="
docker compose up -d

echo ""
echo "✅ Running! Services:"
docker compose ps
echo ""
echo "Bot logs:    docker compose logs -f bot"
echo "Editor logs: docker compose logs -f editor"
echo "Caption editor: http://$(curl -s ifconfig.me 2>/dev/null || echo YOUR_VPS_IP)"
