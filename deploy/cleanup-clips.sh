#!/usr/bin/env bash
# Runs every hour — deletes ALL video files older than 24 hours
CLIP_DIR="/opt/kick_automation/data/clips"
VOD_DIR="/opt/kick_automation/data/vods"

echo "[$(date)] Cleaning videos older than 48 hours..."

# Delete all mp4 files older than 48 hours
find "$CLIP_DIR" -type f -name "*.mp4" -mmin +2880 -delete
find "$VOD_DIR" -type f -name "*.mp4" -mmin +2880 -delete
find "$VOD_DIR" -type f -name "*.ts"  -mmin +2880 -delete

# Remove empty directories
find "$CLIP_DIR" -mindepth 1 -maxdepth 1 -type d -empty -delete
find "$VOD_DIR"  -mindepth 1 -maxdepth 1 -type d -empty -delete

echo "[$(date)] Done. Clips: $(du -sh $CLIP_DIR 2>/dev/null | cut -f1) | VODs: $(du -sh $VOD_DIR 2>/dev/null | cut -f1)"
