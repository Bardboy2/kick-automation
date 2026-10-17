#!/usr/bin/env python3
"""
publish.py — Multi-Platform Social Media Publisher
Uploads approved clips to YouTube Shorts, TikTok, and Instagram Reels.

Backends are selectable per platform via config/.env:
    YOUTUBE_BACKEND=direct|blotato     (default: direct — free, YouTube Data API v3)
    TIKTOK_BACKEND=direct|blotato      (default: direct — free, Content Posting API)
    TIKTOK_MODE=draft|direct           (default: draft — see note below)

TikTok note: posting publicly requires an audited TikTok app with the
`video.publish` scope. Until that review passes, `draft` mode uploads to your
TikTok inbox and you tap publish in the app.

Called by telegram_bot.py on Approve. Can also be run manually:
    python publish.py - --platforms youtube tiktok
"""

import sys
import os
import json
import time
import logging
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / "config" / ".env", override=False)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [publish] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

BLOTATO_BASE = "https://backend.blotato.com/v2"
YT_UPLOAD    = "https://www.googleapis.com/upload/youtube/v3/videos"
TT_BASE      = "https://open.tiktokapis.com/v2/post/publish"


# ─── Metadata helpers (channel-aware — see feedback-captions-tagging) ─────────

def _scrub_owty(text: str) -> str:
    """Remove any owtyofficial/owtygram references from text."""
    import re
    text = re.sub(r"@owty\w*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"#owty\w*", "", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _channel(clip: dict) -> str:
    return clip.get("channel") or (clip.get("metadata_json") or {}).get("channel", "")


def _safe_tags(clip: dict) -> list:
    """Return hashtags with owty tags stripped for non-owtyofficial clips."""
    tags = clip.get("hashtags", [])
    if not isinstance(tags, list):
        return []
    if _channel(clip) == "owtyofficial":
        return tags
    return [t for t in tags if "owty" not in t.lower()]


def _hashtag_str(clip: dict) -> str:
    tags = _safe_tags(clip)
    return " ".join(t if t.startswith("#") else f"#{t}" for t in tags)


def _yt_title(clip: dict) -> str:
    fallback = "Viral moment from Kick" if _channel(clip) == "owtyofficial" else "Viral moment"
    return (clip.get("yt_title") or fallback)[:100]


def _yt_description(clip: dict) -> str:
    fallback = "Caught live on Kick." if _channel(clip) == "owtyofficial" else "Caught live on stream."
    desc = clip.get("yt_description") or fallback
    if _channel(clip) != "owtyofficial":
        desc = _scrub_owty(desc)
    return f"{desc}\n\n{_hashtag_str(clip)}"


def _tt_title(clip: dict) -> str:
    fallback = "Caught this on Kick" if _channel(clip) == "owtyofficial" else "Caught this on stream"
    title = clip.get("tt_title") or clip.get("yt_title") or fallback
    if _channel(clip) != "owtyofficial":
        title = _scrub_owty(title)
    return title[:90]


def _ig_caption(clip: dict) -> str:
    channel  = _channel(clip)
    fallback = "Caught this live on Kick" if channel == "owtyofficial" else "Caught this live on stream"
    cap      = clip.get("ig_caption") or fallback
    if channel != "owtyofficial":
        cap = _scrub_owty(cap)
    tags     = _safe_tags(clip)
    top5     = " ".join(t if t.startswith("#") else f"#{t}" for t in tags[:5])
    tag_line = "\n\n@owtygram" if channel == "owtyofficial" else ""
    return f"{cap}{tag_line}\n\n{top5}"


# ─── YouTube Data API v3 (free) ───────────────────────────────────────────────

def _youtube_access_token() -> str:
    """Exchange the long-lived refresh token for a short-lived access token."""
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id":     os.environ["YOUTUBE_CLIENT_ID"],
            "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
            "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"],
            "grant_type":    "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _publish_youtube_direct(clip: dict) -> dict:
    video_path = clip["approved_path"]
    token      = _youtube_access_token()
    size       = Path(video_path).stat().st_size

    tags = [t.lstrip("#") for t in _safe_tags(clip)][:15]
    metadata = {
        "snippet": {
            "title":       _yt_title(clip),
            "description": _yt_description(clip),
            "tags":        tags,
            "categoryId":  "24",          # Entertainment
        },
        "status": {
            "privacyStatus":           os.environ.get("YOUTUBE_PRIVACY", "public"),
            "selfDeclaredMadeForKids": False,
        },
    }

    # Step 1 — open a resumable upload session
    log.info("[YouTube] Opening resumable upload session…")
    init = requests.post(
        YT_UPLOAD,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization":           f"Bearer {token}",
            "Content-Type":            "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type":   "video/mp4",
        },
        json=metadata,
        timeout=60,
    )
    init.raise_for_status()
    session_url = init.headers["Location"]

    # Step 2 — send the bytes
    log.info("[YouTube] Uploading %.1f MB…", size / 1e6)
    with open(video_path, "rb") as f:
        up = requests.put(
            session_url,
            data=f,
            headers={"Content-Type": "video/mp4", "Content-Length": str(size)},
            timeout=1800,
        )
    up.raise_for_status()
    video_id = up.json().get("id", "")
    log.info("[YouTube] Published: https://youtu.be/%s", video_id)

    return {
        "status":     "published",
        "request_id": video_id,
        "url":        f"https://youtu.be/{video_id}",
    }


# ─── TikTok Content Posting API (free) ────────────────────────────────────────

def _tiktok_access_token() -> str:
    """
    TikTok access tokens live 24h, so always mint a fresh one from the
    refresh token (valid 365 days). TikTok rotates the refresh token on every
    exchange, so the new one is persisted back to .env or the next call fails.
    """
    refresh = os.environ.get("TIKTOK_REFRESH_TOKEN", "")
    if not refresh:
        # No refresh token (older auth run) — fall back to the raw access token.
        return os.environ["TIKTOK_ACCESS_TOKEN"]

    resp = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key":    os.environ["TIKTOK_CLIENT_KEY"],
            "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
            "grant_type":    "refresh_token",
            "refresh_token": refresh,
        },
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    data = body.get("data", body)
    access = data.get("access_token")
    if not access:
        raise RuntimeError(f"TikTok refresh returned no access_token: {str(body)[:300]}")

    new_refresh = data.get("refresh_token")
    if new_refresh and new_refresh != refresh:
        try:
            from dotenv import set_key
            env_file = Path(__file__).parent.parent / "config" / ".env"
            set_key(str(env_file), "TIKTOK_REFRESH_TOKEN", new_refresh)
            set_key(str(env_file), "TIKTOK_ACCESS_TOKEN", access)
            log.info("[TikTok] Rotated refresh token saved.")
        except Exception as exc:
            log.warning("[TikTok] Could not persist rotated token: %s", exc)

    return access


def _publish_tiktok_direct(clip: dict) -> dict:
    video_path = clip["approved_path"]
    token      = _tiktok_access_token()
    size       = Path(video_path).stat().st_size
    mode       = os.environ.get("TIKTOK_MODE", "draft").lower()

    # Public posting needs an audited app; draft lands in the TikTok inbox.
    if mode == "direct":
        endpoint = f"{TT_BASE}/video/init/"
        body = {
            "post_info": {
                "title":          _tt_title(clip),
                "privacy_level":  os.environ.get("TIKTOK_PRIVACY", "PUBLIC_TO_EVERYONE"),
                "disable_duet":   False,
                "disable_stitch": False,
                "disable_comment": False,
            },
            "source_info": {
                "source":            "FILE_UPLOAD",
                "video_size":        size,
                "chunk_size":        size,
                "total_chunk_count": 1,
            },
        }
    else:
        endpoint = f"{TT_BASE}/inbox/video/init/"
        body = {
            "source_info": {
                "source":            "FILE_UPLOAD",
                "video_size":        size,
                "chunk_size":        size,
                "total_chunk_count": 1,
            },
        }

    log.info("[TikTok] Initializing %s upload…", mode)
    init = requests.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json; charset=UTF-8",
        },
        json=body,
        timeout=60,
    )
    init.raise_for_status()
    data       = init.json().get("data", {})
    upload_url = data.get("upload_url")
    publish_id = data.get("publish_id", "")
    if not upload_url:
        raise RuntimeError(f"TikTok init returned no upload_url: {init.text[:300]}")

    log.info("[TikTok] Uploading %.1f MB…", size / 1e6)
    with open(video_path, "rb") as f:
        up = requests.put(
            upload_url,
            data=f,
            headers={
                "Content-Type":  "video/mp4",
                "Content-Range": f"bytes 0-{size - 1}/{size}",
            },
            timeout=1800,
        )
    up.raise_for_status()

    log.info("[TikTok] Upload complete (publish_id=%s, mode=%s)", publish_id, mode)
    return {
        "status":     "published" if mode == "direct" else "draft",
        "request_id": publish_id,
        "note":       "" if mode == "direct" else "Open TikTok → Inbox to publish.",
    }


# ─── Blotato (paid fallback) ──────────────────────────────────────────────────

def _blotato_headers(api_key: str) -> dict:
    return {"blotato-api-key": api_key}


def _blotato_upload(video_path: str, api_key: str) -> str:
    filename = Path(video_path).name
    log.info("[Blotato] Requesting presigned URL for %s…", filename)
    resp = requests.post(
        f"{BLOTATO_BASE}/media/uploads",
        headers={**_blotato_headers(api_key), "Content-Type": "application/json"},
        json={"filename": filename},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    with open(video_path, "rb") as f:
        put = requests.put(data["presignedUrl"], data=f,
                           headers={"Content-Type": "video/mp4"}, timeout=900)
    put.raise_for_status()
    log.info("[Blotato] Upload complete.")
    return data["publicUrl"]


def _blotato_target(platform: str, clip: dict) -> dict:
    if platform == "youtube":
        return {
            "targetType":              "youtube",
            "title":                   _yt_title(clip),
            "privacyStatus":           "public",
            "shouldNotifySubscribers": False,
        }
    if platform == "tiktok":
        return {
            "targetType":       "tiktok",
            "privacyLevel":     "PUBLIC_TO_EVERYONE",
            "disabledComments": False,
            "disabledDuet":     False,
            "disabledStitch":   False,
            "isBrandedContent": False,
            "isYourBrand":      False,
            "isAiGenerated":    False,
        }
    if platform == "instagram":
        return {"targetType": "instagram", "mediaType": "reel"}
    if platform == "facebook":
        return {"targetType": "facebook",
                "pageId": os.environ.get("FB_PAGE_ID", ""),
                "mediaType": "reel"}
    return {"targetType": platform.lower()}


def _publish_blotato(clip: dict, platform: str, public_url: str = None) -> dict:
    api_key = os.environ["BLOTATO_API_KEY"]
    account_id = {
        "youtube":   os.environ.get("BLOTATO_YT_ACCOUNT_ID", ""),
        "tiktok":    os.environ.get("BLOTATO_TT_ACCOUNT_ID", ""),
        "instagram": os.environ.get("BLOTATO_IG_ACCOUNT_ID", ""),
        "facebook":  os.environ.get("BLOTATO_FB_ACCOUNT_ID", ""),
    }.get(platform, "")
    if not account_id:
        return {"status": "skipped", "error": f"No Blotato account ID for {platform}"}

    url = public_url or _blotato_upload(clip["approved_path"], api_key)
    log.info("[Blotato] Posting to %s…", platform)
    resp = requests.post(
        f"{BLOTATO_BASE}/posts",
        headers={**_blotato_headers(api_key), "Content-Type": "application/json"},
        json={"post": {
            "accountId": account_id,
            "content": {
                "text":      _ig_caption(clip),
                "mediaUrls": [url],
                "platform":  platform.lower(),
            },
            "target": _blotato_target(platform, clip),
        }},
        timeout=90,
    )
    resp.raise_for_status()
    body = resp.json()
    return {
        "status":     "published",
        "request_id": str(body.get("id", "")),
        "public_url": url,
    }


# ─── Routing ──────────────────────────────────────────────────────────────────

def _backend_for(platform: str) -> str:
    if platform == "youtube":
        return os.environ.get("YOUTUBE_BACKEND", "direct").lower()
    if platform == "tiktok":
        return os.environ.get("TIKTOK_BACKEND", "direct").lower()
    return "blotato"   # Instagram/Facebook have no free direct path here


def publish_clip(clip: dict, platforms: list[str]) -> dict:
    results = {}
    blotato_url = None          # upload once, reuse across Blotato platforms
    overall = "published"

    for platform in platforms:
        backend = _backend_for(platform)
        try:
            if platform == "youtube" and backend == "direct":
                results[platform] = _publish_youtube_direct(clip)
            elif platform == "tiktok" and backend == "direct":
                results[platform] = _publish_tiktok_direct(clip)
            else:
                if blotato_url is None:
                    blotato_url = _blotato_upload(clip["approved_path"],
                                                  os.environ["BLOTATO_API_KEY"])
                results[platform] = _publish_blotato(clip, platform, blotato_url)
        except Exception as exc:
            log.error("%s failed: %s", platform, exc)
            results[platform] = {"status": "error", "error": str(exc)}
            overall = "partial"

    # Surface the first real id so the Telegram bot has something to show.
    first_id = next((r.get("request_id") for r in results.values()
                     if r.get("request_id")), "")
    if all(r.get("status") == "error" for r in results.values()):
        overall = "error"

    return {
        "platforms":  platforms,
        "status":     overall,
        "request_id": first_id,
        "detail":     results,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Post approved clips to social platforms."
    )
    parser.add_argument("approved_json",
                        help="Path to approved clips JSON, or '-' for stdin")
    parser.add_argument("--platforms", nargs="+",
                        default=["youtube", "tiktok"])
    args = parser.parse_args()

    data = (json.load(sys.stdin) if args.approved_json == "-"
            else json.loads(Path(args.approved_json).read_text()))

    summary = []
    for clip in data.get("clips", []):
        log.info("Publishing clip %s to %s…",
                 clip.get("clip_index", "?"), args.platforms)
        try:
            result = publish_clip(clip, args.platforms)
        except Exception as exc:
            log.error("Publish failed: %s", exc)
            result = {"status": "error", "error": str(exc), "platforms": args.platforms}
        summary.append({
            "clip_index":    clip.get("clip_index"),
            "approved_path": clip.get("approved_path"),
            "result":        result,
        })

    print(json.dumps({"published": summary}, indent=2))


if __name__ == "__main__":
    main()
