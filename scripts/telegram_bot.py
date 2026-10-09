#!/usr/bin/env python3
"""
telegram_bot.py — Telegram Bot for Kick Clip Review
Sends processed clips directly to your Telegram. You approve/reject
with buttons and it posts to YouTube immediately.

Run:
    python scripts/telegram_bot.py
"""

import sys
import os
import json
import time
import logging
import subprocess
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / "config" / ".env", override=True)
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent))
import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [bot] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")
BASE_URL   = f"https://api.telegram.org/bot{BOT_TOKEN}"
SCRIPTS_DIR = Path(__file__).parent

# Track which clip IDs we've already sent to Telegram
SENT_FILE = Path(__file__).parent.parent / "data" / "telegram_sent.json"


def _load_sent() -> set:
    if SENT_FILE.exists():
        return set(json.loads(SENT_FILE.read_text()))
    return set()


def _save_sent(sent: set):
    SENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SENT_FILE.write_text(json.dumps(list(sent)))


# ─── Telegram API helpers ─────────────────────────────────────────────────────

def send_message(text: str, reply_markup: dict = None) -> dict:
    payload = {
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=30)
        return r.json()
    except Exception as exc:
        log.warning("send_message failed (network issue?): %s", exc)
        return {"ok": False, "error": str(exc)}


def _compress_if_needed(video_path: str) -> str:
    """Compress video to under 49MB using target bitrate based on duration."""
    size_mb = Path(video_path).stat().st_size / 1e6
    if size_mb <= 49:
        return video_path
    log.info("Clip is %.1fMB — compressing to fit Telegram limit…", size_mb)
    out = video_path.replace(".mp4", "_compressed.mp4")

    # Get duration
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", video_path],
        capture_output=True, text=True,
    )
    import json as _json
    duration = float(_json.loads(probe.stdout).get("format", {}).get("duration", 300))

    # Target ~40MB: cap at 600k video regardless of duration (safe for up to 10 min)
    # Scale to 480p so the bitrate cap is achievable
    audio_kbps = 96
    video_kbps = max(int((40 * 1_000_000 * 8 - audio_kbps * 1000 * duration) / duration / 1000), 300)
    video_kbps = min(video_kbps, 800)  # never exceed 800k — keeps any clip under 50MB
    log.info("Duration %.0fs → targeting %dk video bitrate (480p)", duration, video_kbps)

    result = subprocess.run(
        ["ffmpeg", "-y", "-i", video_path,
         "-c:v", "libx264", "-b:v", f"{video_kbps}k",
         "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps // 2}k",
         "-preset", "fast", "-vf", "scale=854:480:force_original_aspect_ratio=decrease,pad=854:480:(ow-iw)/2:(oh-ih)/2",
         "-c:a", "aac", "-b:a", f"{audio_kbps}k", "-movflags", "+faststart", out],
        capture_output=True, text=True, timeout=900,
    )
    if result.returncode == 0 and Path(out).stat().st_size > 0:
        final_mb = Path(out).stat().st_size / 1e6
        log.info("Compressed to %.1fMB", final_mb)
        return out
    log.warning("Compression failed — trying original")
    return video_path


def send_video(video_path: str, caption: str, reply_markup: dict = None) -> dict:
    video_path = _compress_if_needed(video_path)
    payload = {
        "chat_id":    CHAT_ID,
        "caption":    caption,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        with open(video_path, "rb") as f:
            r = requests.post(
                f"{BASE_URL}/sendVideo",
                data=payload,
                files={"video": f},
                timeout=600,
            )
        return r.json()
    except Exception as exc:
        log.warning("send_video failed (network issue?): %s", exc)
        return {"ok": False, "error": str(exc)}


def answer_callback(callback_id: str, text: str = "") -> None:
    requests.post(f"{BASE_URL}/answerCallbackQuery",
                  json={"callback_query_id": callback_id, "text": text},
                  timeout=10)


def edit_reply_markup(chat_id: str, message_id: int, markup: dict = None) -> None:
    requests.post(f"{BASE_URL}/editMessageReplyMarkup",
                  json={
                      "chat_id":      chat_id,
                      "message_id":   message_id,
                      "reply_markup": json.dumps(markup or {"inline_keyboard": []}),
                  },
                  timeout=10)


def get_updates(offset: int = None) -> list:
    params = {"timeout": 30, "offset": offset}
    try:
        r = requests.get(f"{BASE_URL}/getUpdates", params=params, timeout=35)
        return r.json().get("result", [])
    except Exception as exc:
        log.warning("getUpdates error: %s", exc)
        return []


# ─── Clip sending ─────────────────────────────────────────────────────────────

def send_clip(clip: dict, platforms: list = None) -> bool:
    """Send a clip video to Telegram with Approve/Reject buttons.

    platforms: if set (e.g. ["instagram"]), shows a platform-specific approve
               button instead of "Post to All".
    """
    clip_id   = clip["id"]
    meta      = clip.get("metadata_json") or {}
    yt_meta   = meta.get("youtube") or {}
    title     = yt_meta.get("title") or f"Clip {clip_id}"
    desc      = yt_meta.get("description") or ""
    hashtags  = " ".join(meta.get("hashtags", []))
    score     = clip.get("score", 0)
    duration  = clip.get("duration", 0)

    video_path = clip.get("captioned_path") or clip.get("clip_path")
    if not video_path or not Path(video_path).exists():
        log.error("Clip %d: video file not found at %s", clip_id, video_path)
        return False

    vod_id = clip.get("vod_id", "unknown")
    start  = clip.get("start", 0)
    end    = clip.get("end", 0)

    def _fmt(s: float) -> str:
        m, sec = divmod(int(s), 60)
        h, m   = divmod(m, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

    clip_num = clip.get("clip_index", 0) + 1

    # Build caption editor URL for local network access
    try:
        import socket as _sock
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        _local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        _local_ip = "localhost"
    editor_url = f"http://{_local_ip}:8888/editor/{clip_id}"

    # Determine what caption versions are available
    has_captions = bool(clip.get("landscape_path"))
    fmt_note = "3 formats: Landscape · Portrait Crop · Portrait BG" if has_captions else "No captions yet"

    caption = (
        f"🎬 <b>Clip #{clip_num}</b>  |  ID: <code>{clip_id}</code>\n\n"
        f"<b>{title}</b>\n\n"
        f"{desc}\n\n"
        f"⏱ {duration:.0f}s  |  🔥 Score: {score:.2f}\n"
        f"📌 VOD: <code>{vod_id}</code>  [{_fmt(start)} → {_fmt(end)}]\n\n"
        f"📝 {fmt_note}\n"
        f"✏️ Edit captions: {editor_url}\n\n"
        f"{hashtags}\n\n"
        f"<i>Cut custom clip:</i>\n"
        f"<code>/clip {vod_id} mm:ss mm:ss</code>"
    )[:1024]

    if platforms and platforms != ["youtube", "tiktok", "instagram"]:
        plat_key  = ",".join(platforms)
        plat_label = " & ".join(p.capitalize() for p in platforms)
        approve_btn = {"text": f"✅ Post to {plat_label}", "callback_data": f"approve:{clip_id}:{plat_key}"}
    else:
        approve_btn = {"text": "✅ Post to All", "callback_data": f"approve:{clip_id}"}

    keyboard = {
        "inline_keyboard": [
            [approve_btn, {"text": "✏️ Add Caption", "callback_data": f"caption:{clip_id}"}],
            [{"text": "✍️ Edit Description", "callback_data": f"editdesc:{clip_id}"}, {"text": "❌ Reject", "callback_data": f"reject:{clip_id}"}],
        ]
    }

    log.info("Sending clip %d to Telegram…", clip_id)
    result = send_video(video_path, caption, reply_markup=keyboard)

    if not result.get("ok"):
        log.error("Failed to send clip %d: %s", clip_id, result)
        return False

    log.info("Clip %d sent successfully.", clip_id)
    return True


# ─── Approval handler ─────────────────────────────────────────────────────────

def handle_approve(clip_id: int, callback_query: dict, platforms: list = None) -> None:
    chat_id    = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    callback_id = callback_query["id"]

    platforms = platforms or ["youtube", "tiktok", "instagram"]
    plat_label = ", ".join(p.capitalize() for p in platforms)

    answer_callback(callback_id, f"Posting to {plat_label}…")
    edit_reply_markup(chat_id, message_id)  # remove buttons

    clip = db.get_clip(clip_id)
    if not clip:
        send_message(f"❌ Clip {clip_id} not found in database.")
        return

    db.approve_clip(clip_id)

    # Build publish payload
    meta = clip.get("metadata_json") or {}
    clip_payload = {
        "clip_index":     clip["clip_index"],
        "approved_path":  clip.get("captioned_path") or clip.get("clip_path"),
        "captioned_path": clip.get("captioned_path") or clip.get("clip_path"),
        "video_path":     clip.get("clip_path"),
        "peak_time":      clip.get("peak_time") or 0,
        "duration":       clip.get("duration") or 0,
        "yt_title":       (meta.get("youtube") or {}).get("title", ""),
        "yt_description": (meta.get("youtube") or {}).get("description", ""),
        "yt_tags":        (meta.get("youtube") or {}).get("tags", []),
        "tt_title":       (meta.get("tiktok") or {}).get("title", ""),
        "ig_caption":     (meta.get("instagram") or {}).get("caption", ""),
        "hashtags":       meta.get("hashtags", []),
        "channel":        meta.get("channel") or clip.get("channel", ""),
    }

    send_message(f"⏳ Uploading to {plat_label}…")

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "publish.py"),
        "-",
        "--platforms", *platforms,
    ]
    try:
        result = subprocess.run(
            cmd,
            input=json.dumps({"clips": [clip_payload]}),
            capture_output=True,
            text=True,
            timeout=400,
        )
        if result.returncode != 0:
            send_message(f"❌ Upload failed:\n<code>{result.stderr[-500:]}</code>")
            return

        data = json.loads(result.stdout)
        db.mark_posted(clip_id, data)

        published = data.get("published", [{}])
        res       = published[0].get("result", {}) if published else {}
        status    = res.get("status", "submitted")
        req_id    = res.get("request_id", "")

        if status in ("published", "done", "success", "completed"):
            send_message("✅ Posted to YouTube Shorts, TikTok & Instagram Reels!")
        else:
            send_message(f"✅ Upload submitted! Status: {status}\nID: <code>{req_id}</code>")

    except subprocess.TimeoutExpired:
        send_message("❌ Upload timed out.")
    except Exception as exc:
        send_message(f"❌ Error: {exc}")


def handle_reject(clip_id: int, callback_query: dict) -> None:
    chat_id    = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    callback_id = callback_query["id"]

    answer_callback(callback_id, "Rejected.")
    edit_reply_markup(chat_id, message_id)
    db.reject_clip(clip_id)
    send_message(f"❌ Clip {clip_id} rejected.")
    log.info("Clip %d rejected.", clip_id)


# ─── /clip command — cut a custom timestamp ───────────────────────────────────

def _parse_time(t: str) -> float:
    """Parse mm:ss or hh:mm:ss into seconds."""
    parts = t.strip().split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return float(parts[0])


def handle_clip_command(text: str) -> None:
    """
    Handle:  /clip <vod_id> <start> <end>
    Example: /clip 107856755 14:30 15:10
    Also:    /clip 107856755 870 930   (seconds)
    """
    parts = text.strip().split()
    # parts: ['/clip', vod_id, start, end]
    if len(parts) < 4:
        send_message(
            "❌ Usage: <code>/clip &lt;vod_id&gt; &lt;start&gt; &lt;end&gt;</code>\n\n"
            "Example: <code>/clip 107856755 14:30 15:10</code>\n"
            "Or in seconds: <code>/clip 107856755 870 930</code>\n\n"
            "Use /vods to see your available VOD IDs."
        )
        return

    vod_id    = parts[1]
    start_str = parts[2]
    end_str   = parts[3]

    try:
        start = _parse_time(start_str)
        end   = _parse_time(end_str)
    except Exception:
        send_message("❌ Invalid time format. Use mm:ss or hh:mm:ss")
        return

    if end <= start:
        send_message("❌ End time must be after start time.")
        return

    if end - start > 180:
        send_message("❌ Maximum clip length is 3 minutes (180 seconds).")
        return

    # Find the source VOD
    base_dir   = Path(__file__).parent.parent
    vod_path   = base_dir / "data" / "downloads" / vod_id / "source.mp4"
    if not vod_path.exists():
        # Search across all downloaded VODs
        send_message(f"❌ VOD <code>{vod_id}</code> not found locally.\nUse /vods to see available VODs.")
        return

    send_message(f"✂️ Cutting clip from <code>{start_str}</code> to <code>{end_str}</code>…")

    # Cut the clip
    out_dir  = base_dir / "data" / "clips" / vod_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"custom_{int(start)}_{int(end)}.mp4"

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "cut_clips.py"),
        "-",
    ]
    peak_data = {
        "clips": [{
            "clip_index": 99,
            "video_path": str(vod_path),
            "start":      start,
            "end":        end,
            "duration":   end - start,
            "peak_time":  (start + end) / 2,
            "score":      1.0,
        }]
    }
    try:
        result = subprocess.run(
            cmd,
            input=json.dumps(peak_data),
            capture_output=True, text=True, timeout=900,
        )
        if result.returncode != 0:
            send_message(f"❌ Cut failed:\n<code>{result.stderr[-300:]}</code>")
            return

        cuts = json.loads(result.stdout)
        if not cuts.get("clips"):
            send_message("❌ Cut returned no clips.")
            return

        clip_path = cuts["clips"][0]["clip_path"]

        # Generate metadata with Claude
        meta_result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "generate_metadata.py"), "-"],
            input=json.dumps(cuts),
            capture_output=True, text=True, timeout=60,
        )
        meta = {}
        if meta_result.returncode == 0:
            enriched = json.loads(meta_result.stdout)
            meta = enriched.get("clips", [{}])[0].get("metadata", {})

        # Save to DB
        clip_dict = {
            **cuts["clips"][0],
            "captioned_path": clip_path,
            "metadata":       meta,
            "metadata_json":  meta,
        }
        clip_id = db.insert_clip(clip_dict, vod_id)

        # Send to Telegram
        clip_record = db.get_clip(clip_id)
        if clip_record:
            send_clip(clip_record)

    except subprocess.TimeoutExpired:
        send_message("❌ Clip generation timed out.")
    except Exception as exc:
        send_message(f"❌ Error: {exc}")
        log.exception("handle_clip_command error")


def _scrape_kick_m3u8(kick_url: str) -> str | None:
    """
    Scrape a Kick VOD page with Chrome impersonation and extract the HLS m3u8 URL.
    Returns the stream.kick.com m3u8 URL, or None if not found.
    """
    import re
    try:
        from curl_cffi import requests as cf_requests
        r = cf_requests.get(kick_url, impersonate="chrome120", timeout=20)
        if r.status_code != 200:
            return None
        # Prefer stream.kick.com URLs (the actual recording, not the signed playback token URL)
        matches = re.findall(
            r'https://stream\.kick\.com/[^\s\'"\\<>]+master\.m3u8', r.text
        )
        if matches:
            return matches[0]
        # Fallback: any m3u8
        matches = re.findall(r'https://[^\s\'"\\<>]+\.m3u8[^\s\'"\\<>]*', r.text)
        return matches[0] if matches else None
    except Exception as e:
        log.warning("_scrape_kick_m3u8 failed: %s", e)
        return None


def _fetch_recent_vods(channel: str, limit: int = 5) -> list[tuple[str, str]]:
    """Fetch recent available VODs for a Kick channel via the API. Returns [(title, url), ...]."""
    import urllib.request
    try:
        api_url = f"https://kick.com/api/v2/channels/{channel}/videos?page=1&limit={limit}"
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        results = []
        for v in (data if isinstance(data, list) else []):
            video = v.get("video") or {}
            uuid  = video.get("uuid") if isinstance(video, dict) else None
            title = v.get("session_title") or "Untitled"
            if uuid:
                results.append((title[:40], f"https://kick.com/{channel}/videos/{uuid}"))
        return results
    except Exception as e:
        log.warning("Could not fetch recent VODs for %s: %s", channel, e)
        return []


def handle_link_command(text: str, channel: str = None, vertical: bool = False, watermark: bool = False) -> None:
    """
    Handle a pasted Kick URL with optional timeframe.

    Formats accepted:
        https://kick.com/owtyofficial/videos/abc123  14:30  15:10
        https://kick.com/video/abc123  870  930
        https://kick.com/owtyofficial/videos/abc123        ← no times = full auto-detect

    channel: the Kick handle whose watermark to embed (defaults to env KICK_CHANNEL_SLUG).
    """
    import re, shutil
    parts = text.strip().split()
    url   = parts[0]
    channel = channel or os.environ.get("KICK_CHANNEL_SLUG", "owtyofficial")

    # Extract VOD ID from URL
    match = re.search(r'/videos?/([a-zA-Z0-9_-]+)', url)
    if not match:
        send_message("❌ Couldn't find a VOD ID in that link.\nFormat: <code>https://kick.com/.../videos/ID  14:30  15:10</code>")
        return

    vod_id    = match.group(1)
    base_dir  = Path(__file__).parent.parent
    vod_dir   = base_dir / "data" / "downloads" / vod_id
    vod_path  = vod_dir / "source.mp4"

    # ── Download if not already on disk ──────────────────────────────────────
    if not vod_path.exists():
        send_message(f"⬇️ Downloading VOD <code>{vod_id}</code>… this may take a few minutes.")
        vod_dir.mkdir(parents=True, exist_ok=True)
        ytdlp = shutil.which("yt-dlp") or str(base_dir / ".venv" / "bin" / "yt-dlp")

        # First attempt: yt-dlp with its Kick extractor
        dl = subprocess.run(
            [ytdlp, "--no-warnings", "-o", str(vod_path), url],
            capture_output=True, text=True, timeout=1800,
        )

        # If yt-dlp's extractor fails, scrape the m3u8 from the page directly
        if dl.returncode != 0 or not vod_path.exists():
            log.info("yt-dlp extractor failed — trying page scrape for m3u8")
            m3u8_url = _scrape_kick_m3u8(url)
            if m3u8_url:
                log.info("Found m3u8 via scrape: %s", m3u8_url[:80])
                dl2 = subprocess.run(
                    [ytdlp, "--no-warnings", "-o", str(vod_path), m3u8_url],
                    capture_output=True, text=True, timeout=1800,
                )
                if dl2.returncode == 0 and vod_path.exists():
                    dl = dl2  # success via scrape fallback
                else:
                    dl = dl2  # still failed — use dl2 error for reporting
            else:
                log.warning("Could not find m3u8 on page — VOD may be deleted")

        if dl.returncode != 0 or not vod_path.exists():
            # Clean up empty dir
            try:
                vod_dir.rmdir()
            except Exception:
                pass
            is_404 = "404" in (dl.stderr or "") or "Not Found" in (dl.stderr or "")
            if is_404 or not vod_path.exists():
                suggestions = _fetch_recent_vods(channel)
                msg = "❌ <b>Could not download that VOD.</b> It may be deleted or private.\n\n"
                if suggestions:
                    msg += "Here are <b>recent available VODs</b>:\n\n"
                    for title, link in suggestions:
                        msg += f"• <a href='{link}'>{title}</a>\n"
                    msg += "\nPaste one of those links."
                else:
                    msg += "No recent VODs found. Go to the channel's Videos page and copy a current link."
            else:
                msg = f"❌ Download failed:\n<code>{dl.stderr[-300:]}</code>"
            send_message(msg)
            return

        mb = vod_path.stat().st_size / 1e6
        send_message(f"✅ Downloaded ({mb:.0f} MB). Processing…")
    else:
        mb = vod_path.stat().st_size / 1e6
        log.info("VOD %s already on disk (%.0f MB)", vod_id, mb)

    # ── Timeframe provided → cut specific clip ────────────────────────────────
    if len(parts) >= 3:
        start_str = parts[1]
        end_str   = parts[2]
        try:
            start = _parse_time(start_str)
            end   = _parse_time(end_str)
        except Exception:
            send_message("❌ Invalid time format. Use mm:ss or hh:mm:ss")
            return
        if end <= start:
            send_message("❌ End time must be after start time.")
            return
        if end - start > 180:
            send_message("❌ Max clip length is 3 minutes.")
            return

        send_message(f"✂️ Cutting <code>{start_str}</code> → <code>{end_str}</code>…")

        peak_data = {"clips": [{
            "clip_index": 99, "video_path": str(vod_path),
            "start": start, "end": end, "duration": end - start,
            "peak_time": (start + end) / 2, "score": 1.0,
        }]}
        try:
            cut_cmd = [sys.executable, str(SCRIPTS_DIR / "cut_clips.py"), "-"]
            if not vertical:
                cut_cmd.append("--landscape")
            if not watermark:
                cut_cmd.append("--no-watermark")
            res = subprocess.run(
                cut_cmd,
                input=json.dumps(peak_data), capture_output=True, text=True, timeout=900,
            )
            if res.returncode != 0:
                send_message(f"❌ Cut failed:\n<code>{res.stderr[-300:]}</code>")
                return
            cuts = json.loads(res.stdout)
            if not cuts.get("clips"):
                send_message("❌ No clip produced.")
                return

            clip_path = cuts["clips"][0]["clip_path"]

            meta_env = {**os.environ, "KICK_CHANNEL_SLUG": channel or "streamer"}
            meta_res = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "generate_metadata.py"), "-"],
                input=json.dumps(cuts), capture_output=True, text=True, timeout=60,
                env=meta_env,
            )
            meta = {}
            if meta_res.returncode == 0:
                enriched = json.loads(meta_res.stdout)
                meta = enriched.get("clips", [{}])[0].get("metadata", {})

            meta["channel"] = channel
            clip_id = db.insert_clip({
                **cuts["clips"][0],
                "captioned_path": clip_path,
                "metadata": meta,
                "metadata_json": meta,
            }, vod_id)
            clip_record = db.get_clip(clip_id)
            if clip_record:
                send_clip(clip_record)

        except subprocess.TimeoutExpired:
            send_message("❌ Timed out cutting clip.")
        except Exception as exc:
            send_message(f"❌ Error: {exc}")
            log.exception("handle_link_command cut error")

    # ── No timeframe → run full auto-detect pipeline ──────────────────────────
    else:
        send_message(f"🔍 No timeframe given — running auto-detect on <code>{vod_id}</code>…")
        try:
            pipeline_cmd = [sys.executable, str(SCRIPTS_DIR / "pipeline.py"),
                            "--vod-id", vod_id, "--vod-path", str(vod_path),
                            "--channel", channel]
            if not vertical:
                pipeline_cmd.append("--landscape")
            if not watermark:
                pipeline_cmd.append("--no-watermark")
            res = subprocess.run(
                pipeline_cmd,
                capture_output=True, text=True, timeout=3600,
            )
            if res.returncode != 0:
                send_message(f"❌ Pipeline failed:\n<code>{res.stderr[-300:]}</code>")
                return
            result = json.loads(res.stdout)
            n = result.get("clips", 0)
            send_message(f"✅ Pipeline done — {n} clip(s) ready. Sending to you now…")
        except subprocess.TimeoutExpired:
            send_message("❌ Pipeline timed out.")
        except Exception as exc:
            send_message(f"❌ Error: {exc}")
            log.exception("handle_link_command pipeline error")


def handle_caption_input(clip_id: int, caption_text: str) -> None:
    """Burn a caption overlay onto the clip and re-send the updated video."""
    clip = db.get_clip(clip_id)
    if not clip:
        send_message(f"❌ Clip {clip_id} not found in database.")
        return

    source_path = clip.get("captioned_path") or clip.get("clip_path")
    if not source_path or not Path(source_path).exists():
        send_message(f"❌ Video file for clip {clip_id} not found.")
        return

    send_message(f"🎨 Burning caption onto clip {clip_id}…")

    # Output path: strip any existing _cap suffix so re-captioning doesn't stack
    src = Path(source_path)
    stem = src.stem
    while stem.endswith("_cap"):
        stem = stem[:-4]
    final_path = str(src.parent / f"{stem}_cap.mp4")
    # Write to a temp file first to avoid overwriting input
    tmp_path = str(src.parent / f"{stem}_cap_tmp.mp4")

    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "burn_caption.py"),
         "--video", source_path,
         "--text",  caption_text,
         "--output", tmp_path],
        capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0:
        send_message(f"❌ Caption burn failed:\n<code>{result.stderr[-400:]}</code>")
        return

    Path(tmp_path).replace(final_path)
    out_path = final_path
    db.update_captioned_path(clip_id, out_path)
    send_message("✅ Caption added! Here's the updated clip — approve or change it:")
    clip_record = db.get_clip(clip_id)
    if clip_record:
        send_clip(clip_record)


def handle_vods_command() -> None:
    """List all locally downloaded VODs."""
    base_dir     = Path(__file__).parent.parent
    download_dir = base_dir / "data" / "downloads"
    vods = sorted(download_dir.iterdir()) if download_dir.exists() else []
    if not vods:
        send_message("No VODs downloaded yet.")
        return
    lines = ["📂 <b>Available VODs:</b>\n"]
    for v in vods:
        src = v / "source.mp4"
        if src.exists():
            mb = src.stat().st_size / 1e6
            lines.append(f"• <code>{v.name}</code> ({mb:.0f} MB)")
    lines.append("\nUse: <code>/clip &lt;vod_id&gt; &lt;start&gt; &lt;end&gt;</code>")
    send_message("\n".join(lines))


# ─── Main loop ────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN or not CHAT_ID:
        log.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in config/.env")
        sys.exit(1)

    log.info("Telegram bot started. Waiting for clips…")
    send_message("🤖 <b>Kick Automation Bot is online!</b>\nI'll send you clips to review as soon as they're ready.")

    offset          = None
    sent            = _load_sent()
    check_tick      = 0     # check for new clips every 30s
    pending_link    = None  # URL waiting for handle confirmation
    pending_caption = None  # clip_id waiting for caption text input
    pending_editdesc = None # clip_id waiting for new description text

    consecutive_errors = 0

    while True:
        try:
            # ── Poll for messages and button presses ─────────────────────────
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1

                try:
                    # Handle inline button presses
                    cq = update.get("callback_query")
                    if cq:
                        data = cq.get("data", "")
                        if ":" in data:
                            parts2  = data.split(":")
                            action  = parts2[0]
                            clip_id = int(parts2[1])
                            plats   = parts2[2].split(",") if len(parts2) > 2 else None
                            if action == "approve":
                                handle_approve(clip_id, cq, platforms=plats)
                            elif action == "reject":
                                handle_reject(clip_id, cq)
                            elif action == "caption":
                                chat_id_cq    = cq["message"]["chat"]["id"]
                                message_id_cq = cq["message"]["message_id"]
                                answer_callback(cq["id"], "Type your caption text")
                                edit_reply_markup(chat_id_cq, message_id_cq)
                                pending_caption  = clip_id
                                pending_editdesc = None
                                pending_link     = None
                                send_message(
                                    f"✏️ <b>Type the caption for Clip {clip_id}:</b>\n\n"
                                    "It will appear as a white box at the top of the video.\n"
                                    "<i>Example: Jarvis said IShowSpeed didn't ignore Peller</i>"
                                )
                            elif action == "editdesc":
                                chat_id_cq    = cq["message"]["chat"]["id"]
                                message_id_cq = cq["message"]["message_id"]
                                answer_callback(cq["id"], "Type new description")
                                edit_reply_markup(chat_id_cq, message_id_cq)
                                pending_editdesc = clip_id
                                pending_caption  = None
                                pending_link     = None
                                clip_now = db.get_clip(clip_id)
                                meta = clip_now.get("metadata_json") or {}
                                if isinstance(meta, str):
                                    import json as _json
                                    meta = _json.loads(meta)
                                current_desc = (meta.get("instagram") or {}).get("caption", "")
                                send_message(
                                    f"✍️ <b>Edit description for Clip {clip_id}:</b>\n\n"
                                    f"Current:\n<i>{current_desc or '(none)'}</i>\n\n"
                                    "Type the new description to use for all platforms:"
                                )
                        continue

                    # Handle text commands
                    msg  = update.get("message", {})
                    text = msg.get("text", "").strip()
                    uid  = str(msg.get("from", {}).get("id", ""))

                    if not text:
                        continue
                    log.info("Incoming msg from uid=%s CHAT_ID=%s text=%r", uid, CHAT_ID, text[:80])
                    if uid != CHAT_ID:
                        log.warning("Ignoring message from uid=%s (not CHAT_ID=%s)", uid, CHAT_ID)
                        continue

                    if (text.startswith("https://kick.com") or text.startswith("http://kick.com")
                            or text.startswith("https://www.twitch.tv") or text.startswith("https://twitch.tv")):
                        pending_link    = text
                        pending_caption = None
                        platform_hint   = "Twitch" if "twitch.tv" in text else "Kick"
                        send_message(
                            f"🎯 <b>What's the {platform_hint} handle for this stream?</b>\n"
                            "Reply with just the username (e.g. <code>carterefe</code>)\n"
                            "so I can label it correctly."
                        )
                    elif pending_link and not text.startswith("/"):
                        # User replied with the handle
                        handle = text.strip().lstrip("@").lower()
                        is_twitch = "twitch.tv" in pending_link
                        os.environ["KICK_CHANNEL_SLUG"] = handle
                        if is_twitch:
                            send_message(f"✅ Got it — processing <b>{handle}</b>'s stream (no watermark). Processing…")
                        else:
                            send_message(f"✅ Got it — using <b>kick.com/{handle}</b> as the creator. Processing…")
                        _link = pending_link
                        pending_link = None
                        import threading
                        threading.Thread(
                            target=handle_link_command,
                            kwargs={"text": _link, "channel": handle},
                            daemon=True,
                        ).start()
                    elif pending_editdesc is not None and not text.startswith("/"):
                        # User typed a new description after clicking ✍️ Edit Description
                        import json as _json
                        cid              = pending_editdesc
                        pending_editdesc = None
                        clip_now = db.get_clip(cid)
                        meta = clip_now.get("metadata_json") or {}
                        if isinstance(meta, str):
                            meta = _json.loads(meta)
                        meta.setdefault("youtube", {})["description"] = text
                        meta.setdefault("tiktok", {})["title"] = text[:150]
                        meta.setdefault("instagram", {})["caption"] = text
                        with db.get_connection() as conn:
                            conn.execute("UPDATE clips SET metadata_json=? WHERE id=?",
                                         (_json.dumps(meta), cid))
                        send_message(f"✅ Description updated! Here's the clip — ready to post:")
                        updated_clip = db.get_clip(cid)
                        if updated_clip:
                            send_clip(updated_clip)
                    elif pending_caption is not None and not text.startswith("/"):
                        # User typed a caption after clicking ✏️ Add Caption
                        cid             = pending_caption
                        pending_caption = None
                        handle_caption_input(cid, text)
                    elif text.startswith("/clip"):
                        handle_clip_command(text)
                    elif text.startswith("/vods"):
                        handle_vods_command()
                    elif text.startswith("/help") or text.startswith("/start"):
                        send_message(
                            "🤖 <b>Kick Automation Bot</b>\n\n"
                            "<b>Paste a Kick link:</b>\n"
                            "<code>https://kick.com/.../videos/ID  14:30  15:10</code>\n"
                            "→ Downloads (if needed) and cuts that exact timeframe.\n\n"
                            "<code>https://kick.com/.../videos/ID</code>\n"
                            "→ Auto-detects best viral moments.\n\n"
                            "<b>Commands:</b>\n"
                            "/vods — list downloaded VODs\n"
                            "/clip &lt;vod_id&gt; &lt;start&gt; &lt;end&gt; — cut from a downloaded VOD\n\n"
                            "<b>Example:</b>\n"
                            "<code>https://kick.com/owtyofficial/videos/c24d278e  14:30  15:10</code>"
                        )
                except Exception:
                    log.exception("Error handling update %s — continuing.",
                                  update.get("update_id"))

            # ── Check for new pending clips every 30s ────────────────────────
            check_tick += 1
            if check_tick >= 3:   # 3 × ~10s polling cycle ≈ 30s
                check_tick = 0
                try:
                    pending = db.get_pending_clips()
                    if pending:
                        # Only send clips from the single most recently processed
                        # VOD. Clips are ordered by created_at DESC, so the first
                        # clip's vod_id is the newest batch.
                        latest_vod_id = pending[0]["vod_id"]
                        pending = [c for c in pending if c["vod_id"] == latest_vod_id]

                    for clip in pending:
                        cid = clip["id"]
                        if str(cid) not in sent:
                            try:
                                if send_clip(clip):
                                    sent.add(str(cid))
                                    _save_sent(sent)
                            except Exception:
                                log.exception("Failed to send clip %d — will retry next cycle.", cid)
                except Exception:
                    log.exception("Error while checking pending clips — continuing.")

            consecutive_errors = 0
            time.sleep(1)

        except Exception:
            consecutive_errors += 1
            backoff = min(60, 5 * consecutive_errors)
            log.exception("Unexpected error in main loop (attempt %d) — "
                          "retrying in %ds.", consecutive_errors, backoff)
            time.sleep(backoff)


if __name__ == "__main__":
    main()
