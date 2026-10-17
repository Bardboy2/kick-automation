#!/usr/bin/env python3
"""
monitor.py — Multi-Platform VOD Monitor (Kick + Twitch)
Polls your channels for new VODs, downloads them, and fires the pipeline.

Run modes:
    python scripts/monitor.py           # daemon — polls every POLL_INTERVAL seconds
    python scripts/monitor.py --once    # single poll, then exit (cron-friendly)

Watermark policy (see feedback-no-watermark-pasted):
    Kick/owtyofficial  → watermark burned in
    Everything else    → --no-watermark (legal risk on other creators' content)
"""

import os
import re
import sys
import json
import time
import logging
import subprocess
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / "config" / ".env", override=False)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [monitor] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

def _env_int(key: str, default: str) -> int:
    """systemd's EnvironmentFile keeps inline '# comments' in the value."""
    raw = os.environ.get(key, default).split("#")[0].strip()
    return int(raw or default)


BASE_DIR      = Path(__file__).parent.parent
SCRIPTS_DIR   = Path(__file__).parent
DOWNLOAD_DIR  = Path(os.environ.get("DOWNLOAD_DIR", str(BASE_DIR / "data" / "downloads")))
STATE_FILE    = Path(os.environ.get("STATE_FILE", str(BASE_DIR / "data" / "state" / "seen_vods.json")))

KICK_CHANNEL    = os.environ.get("KICK_CHANNEL_SLUG", "")
TWITCH_CHANNEL  = os.environ.get("TWITCH_CHANNEL", "")
TWITCH_ID       = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_SECRET   = os.environ.get("TWITCH_CLIENT_SECRET", "")
POLL_INTERVAL   = _env_int("POLL_INTERVAL", "300")

TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT   = os.environ.get("TELEGRAM_CHAT_ID", "")

# Only this Kick channel gets the watermark — never anyone else's content.
WATERMARK_CHANNEL = "owtyofficial"


# ─── State ────────────────────────────────────────────────────────────────────

def load_seen() -> set:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except json.JSONDecodeError:
            log.warning("State file corrupt — starting fresh.")
    return set()


def save_seen(seen: set):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(seen)))


def notify(text: str):
    """Best-effort Telegram ping so you know the monitor picked something up."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=20,
        )
    except requests.RequestException as exc:
        log.warning("Telegram notify failed: %s", exc)


# ─── Kick ─────────────────────────────────────────────────────────────────────

def fetch_kick_vods(channel: str) -> list[dict]:
    """Kick has no official API — this hits the undocumented v2 endpoint."""
    try:
        resp = requests.get(
            f"https://kick.com/api/v2/channels/{channel}/videos",
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, json.JSONDecodeError) as exc:
        log.error("Kick VOD fetch failed for @%s: %s", channel, exc)
        return []

    vods = []
    for item in (data if isinstance(data, list) else data.get("data", [])):
        video = item.get("video") or {}
        uuid  = video.get("uuid") if isinstance(video, dict) else None
        if not uuid:
            continue
        vods.append({
            "platform": "kick",
            "id":       uuid,
            "url":      f"https://kick.com/{channel}/videos/{uuid}",
            "title":    item.get("session_title") or uuid,
            "channel":  channel,
        })
    return vods


def scrape_kick_m3u8(kick_url: str) -> str | None:
    """
    Kick sits behind Cloudflare and yt-dlp's extractor fails on it, so we
    impersonate Chrome and pull the HLS URL straight out of the page.
    """
    try:
        from curl_cffi import requests as cf_requests
        r = cf_requests.get(kick_url, impersonate="chrome120", timeout=25)
        if r.status_code != 200:
            return None
        matches = re.findall(r'https://stream\.kick\.com/[^\s\'"\\<>]+master\.m3u8', r.text)
        if matches:
            return matches[0]
        matches = re.findall(r'https://[^\s\'"\\<>]+\.m3u8[^\s\'"\\<>]*', r.text)
        return matches[0] if matches else None
    except Exception as exc:
        log.warning("Kick m3u8 scrape failed: %s", exc)
        return None


# ─── Twitch ───────────────────────────────────────────────────────────────────

def twitch_token() -> str | None:
    """App access token via client_credentials — no user consent needed."""
    if not TWITCH_ID or not TWITCH_SECRET:
        return None
    try:
        resp = requests.post(
            "https://id.twitch.tv/oauth2/token",
            params={
                "client_id":     TWITCH_ID,
                "client_secret": TWITCH_SECRET,
                "grant_type":    "client_credentials",
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except requests.RequestException as exc:
        log.error("Twitch token request failed: %s", exc)
        return None


def fetch_twitch_vods(channel: str) -> list[dict]:
    token = twitch_token()
    if not token:
        log.warning("Twitch credentials missing/invalid — skipping Twitch.")
        return []

    headers = {"Client-ID": TWITCH_ID, "Authorization": f"Bearer {token}"}
    try:
        u = requests.get("https://api.twitch.tv/helix/users",
                         params={"login": channel}, headers=headers, timeout=20)
        u.raise_for_status()
        users = u.json().get("data", [])
        if not users:
            log.error("Twitch channel '%s' not found.", channel)
            return []
        user_id = users[0]["id"]

        v = requests.get("https://api.twitch.tv/helix/videos",
                         params={"user_id": user_id, "type": "archive", "first": 5},
                         headers=headers, timeout=20)
        v.raise_for_status()
        videos = v.json().get("data", [])
    except (requests.RequestException, json.JSONDecodeError) as exc:
        log.error("Twitch VOD fetch failed for %s: %s", channel, exc)
        return []

    return [{
        "platform": "twitch",
        "id":       vid["id"],
        "url":      vid.get("url") or f"https://www.twitch.tv/videos/{vid['id']}",
        "title":    vid.get("title") or vid["id"],
        "channel":  channel,
    } for vid in videos]


# ─── Download ─────────────────────────────────────────────────────────────────

def download_vod(vod: dict) -> Path | None:
    vod_id   = vod["id"]
    out_dir  = DOWNLOAD_DIR / vod_id
    out_path = out_dir / "source.mp4"

    if out_path.exists() and out_path.stat().st_size > 10_000_000:
        log.info("VOD %s already downloaded — skipping.", vod_id)
        return out_path

    out_dir.mkdir(parents=True, exist_ok=True)
    ytdlp = str(BASE_DIR / ".venv" / "bin" / "yt-dlp")
    log.info("Downloading %s VOD %s…", vod["platform"], vod_id)

    def _run(target: str) -> bool:
        res = subprocess.run(
            [ytdlp, "--no-warnings", "--no-playlist", "-o", str(out_path), target],
            capture_output=True, text=True, timeout=5400,
        )
        if res.returncode != 0:
            log.warning("yt-dlp failed: %s", (res.stderr or "")[-400:])
        return res.returncode == 0 and out_path.exists()

    ok = _run(vod["url"])

    # Kick's extractor is unreliable — fall back to scraping the HLS URL.
    if not ok and vod["platform"] == "kick":
        log.info("Falling back to Kick m3u8 scrape…")
        m3u8 = scrape_kick_m3u8(vod["url"])
        if m3u8:
            ok = _run(m3u8)
        else:
            log.error("No m3u8 found — VOD may be deleted or private.")

    if not ok:
        # Leave nothing half-written behind for the next cycle to trip over.
        if out_path.exists():
            out_path.unlink()
        try:
            out_dir.rmdir()
        except OSError:
            pass
        return None

    log.info("Downloaded VOD %s (%.0f MB)", vod_id, out_path.stat().st_size / 1e6)
    return out_path


# ─── Pipeline ─────────────────────────────────────────────────────────────────

def start_pipeline(vod: dict, local_path: Path):
    channel  = vod["channel"]
    is_owty  = vod["platform"] == "kick" and channel.lower() == WATERMARK_CHANNEL

    cmd = [
        sys.executable, str(SCRIPTS_DIR / "pipeline.py"),
        "--vod-id",    vod["id"],
        "--vod-path",  str(local_path),
        "--vod-title", vod["title"],
        "--channel",   channel,
        "--landscape",
    ]
    if not is_owty:
        cmd.append("--no-watermark")

    log.info("Starting pipeline for %s VOD %s (watermark=%s)",
             vod["platform"], vod["id"], is_owty)
    # Detached so a monitor restart can't kill an in-flight pipeline.
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    log.info("Pipeline PID %d", proc.pid)
    return proc


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_once():
    if not KICK_CHANNEL and not TWITCH_CHANNEL:
        log.error("Set KICK_CHANNEL_SLUG and/or TWITCH_CHANNEL in config/.env.")
        return

    seen = load_seen()
    vods = []
    # Twitch first — it's the more reliable source when both have a new VOD.
    if TWITCH_CHANNEL:
        vods += fetch_twitch_vods(TWITCH_CHANNEL)
    if KICK_CHANNEL:
        vods += fetch_kick_vods(KICK_CHANNEL)

    new = [v for v in vods if f"{v['platform']}:{v['id']}" not in seen]
    if not new:
        log.info("No new VODs.")
        return

    vod = new[0]   # newest only — one pipeline at a time keeps the VPS sane
    key = f"{vod['platform']}:{vod['id']}"
    log.info("New %s VOD: %s", vod["platform"], vod["title"][:60])
    notify(f"🆕 New {vod['platform'].title()} VOD detected:\n<b>{vod['title'][:80]}</b>\n\n⬇️ Downloading…")

    path = download_vod(vod)
    if not path:
        # Mark it seen anyway — a dead VOD would otherwise retry forever.
        seen.add(key)
        save_seen(seen)
        notify(f"❌ Could not download <b>{vod['title'][:60]}</b> — it may be deleted or private.")
        return

    start_pipeline(vod, path)
    seen.add(key)
    save_seen(seen)
    notify(f"✅ Downloaded ({path.stat().st_size / 1e6:.0f} MB). Finding viral moments…")


def main():
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Monitor started. Kick=@%s  Twitch=%s  Interval=%ds",
             KICK_CHANNEL or "off", TWITCH_CHANNEL or "off", POLL_INTERVAL)

    if "--once" in sys.argv:
        run_once()
        return

    while True:
        try:
            run_once()
        except Exception:
            log.exception("Monitor loop error — will retry.")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
