#!/usr/bin/env bash
# Runs daily at 3am — deletes approved/rejected clips older than 7 days
# Keeps pending clips untouched
CLIP_DIR="/opt/kick_automation/data/clips"
KEEP_DAYS=7

echo "[$(date)] Cleaning clips older than ${KEEP_DAYS} days..."

# Delete clip files linked to approved/rejected DB entries older than KEEP_DAYS
python3 /opt/kick_automation/scripts/db.py --cleanup-old-clips "$KEEP_DAYS" 2>/dev/null || true

# Also hard-delete any stray files older than 14 days regardless of DB status
find "$CLIP_DIR" -type f -name "*.mp4" -mtime +14 -delete

# Remove empty VOD directories
find "$CLIP_DIR" -mindepth 1 -maxdepth 1 -type d -empty -delete

echo "[$(date)] Cleanup done. Disk usage: $(du -sh $CLIP_DIR 2>/dev/null | cut -f1)"
