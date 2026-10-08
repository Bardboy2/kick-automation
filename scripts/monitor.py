#!/usr/bin/env python3
"""
monitor.py — Kick Channel VOD Monitor
Polls your Kick channel for new VODs, downloads them, and fires the pipeline.

Run modes:
    python scripts/monitor.py           # daemon — polls every POLL_INTERVAL seconds
    python scripts/monitor.py --once    # single poll, then exit (cron-friendly)

After each VOD is processed, clips appear in the dashboard:
    python dashboard/app.py
"""

import os
import sys
import json
import time
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / "config" / ".env", override=True)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [monitor] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_DIR      = Path(__file__).parent.parent
SCRIPTS_DIR   = Path(__file__).parent
DOWNLOAD_DIR  = Path(os.environ.get("DOWNLOAD_DIR",
                     str(BASE_DIR / "data" / "downloads")))
STATE_FILE    = Path(os.environ.get("STATE_FILE",
                     str(BASE_DIR / "data" / "state" / "seen_vods.json")))
KICK_CHANNEL  = os.environ.get("KICK_CHANNEL_SLUG", "")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))
MAX_DOWNLOADS = int(os.environ.get("MAX_DOWNLOADS", "3"))


# ─── State management ─────────────────────────────────────────────────────────

def load_seen() -> set:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_seen(seen: set):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(list(seen)))


# ─── Kick API ─────────────────────────────────────────────────────────────────

def fetch_vods(channel: str) -> list[dict]:
    """Fetch recent VODs from Kick's public API."""
    url = f"https://kick.com/api/v2/channels/{channel}/videos"
    try:
        resp = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 "
                              "KickClipBot/1.0",
                "Accept":     "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])
    except requests.RequestException as exc:
        log.error("Failed to fetch VODs from Kick: %s", exc)
        return []


# ─── Download ─────────────────────────────────────────────────────────────────

def download_vod(vod: dict) -> object:
    """Download a VOD with yt-dlp. Returns local path or None on failure."""
    vod_id   = str(vod["id"])
    vod_url  = vod.get("url") or vod.get("source") or f"https://kick.com/video/{vod_id}"
    out_dir  = DOWNLOAD_DIR / vod_id
    out_path = out_dir / "source.mp4"

    if out_path.exists():
        log.info("VOD %s already downloaded — skipping.", vod_id)
        return out_path

    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("Downloading VOD %s…", vod_id)
    ytdlp = str(Path(__file__).parent.parent / ".venv" / "bin" / "yt-dlp")
    cmd = [
        ytdlp,
        "--format",               "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format",  "mp4",
        "--output",               str(out_path),
        "--no-playlist",
        "--quiet",
        "--progress",
        vod_url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("yt-dlp failed for VOD %s:\n%s", vod_id, result.stderr[-2000:])
        return None

    log.info("Downloaded VOD %s (%.1f MB)", vod_id, out_path.stat().st_size / 1e6)
    return out_path


# ─── Pipeline trigger ─────────────────────────────────────────────────────────

def run_pipeline(vod: dict, local_path: Path):
    """Fire pipeline.py for the downloaded VOD (non-blocking subprocess)."""
    vod_id    = str(vod["id"])
    vod_title = vod.get("session_title") or vod.get("title") or vod_id

    log.info("Starting pipeline for VOD %s: %s", vod_id, vod_title[:60])
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "pipeline.py"),
        "--vod-id",    vod_id,
        "--vod-path",  str(local_path),
        "--vod-title", vod_title,
    ]
    # Run in background so monitor can continue polling
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    log.info("Pipeline started (PID %d) for VOD %s.", proc.pid, vod_id)
    return proc


# ─── Main loop ────────────────────────────────────────────────────────────────

def run_once():
    if not KICK_CHANNEL:
        log.error("KICK_CHANNEL_SLUG is not set. Add it to config/.env.")
        return

    seen     = load_seen()
    vods     = fetch_vods(KICK_CHANNEL)
    new_vods = [v for v in vods if str(v.get("id", "")) not in seen]

    if not new_vods:
        log.info("No new VODs found for @%s.", KICK_CHANNEL)
        return

    # Only process the single latest VOD
    new_vods = new_vods[:1]
    log.info("Found new VOD for @%s — processing latest only.", KICK_CHANNEL)
    procs = []

    for vod in new_vods:
        path = download_vod(vod)
        if path:
            proc = run_pipeline(vod, path)
            procs.append(proc)
            seen.add(str(vod["id"]))
            save_seen(seen)

    if procs:
        log.info(
            "%d pipeline(s) running in background. "
            "Open the dashboard when done:  python dashboard/app.py",
            len(procs),
        )


def main():
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    log.info(
        "Kick monitor started. Channel=@%s  Interval=%ds",
        KICK_CHANNEL or "NOT SET", POLL_INTERVAL,
    )

    if "--once" in sys.argv:
        run_once()
        return

    # Daemon mode
    while True:
        try:
            run_once()
        except Exception:
            log.exception("Unexpected error in monitor loop — will retry.")
        log.info("Sleeping %ds…", POLL_INTERVAL)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
