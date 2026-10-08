#!/usr/bin/env python3
"""
publish.py — Multi-Platform Social Media Publisher (via Blotato API)
Uploads approved clips to YouTube Shorts, TikTok, and Instagram Reels.

Called by dashboard/app.py automatically on Approve.
Can also be run manually:
    python publish.py <approved_json_path_or_stdin> --platforms youtube tiktok instagram
"""

import sys
import os
import json
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


# ─── Metadata helpers ─────────────────────────────────────────────────────────

def _scrub_owty(text: str) -> str:
    """Remove any owtyofficial/owtygram references from text."""
    import re
    text = re.sub(r"@owty\w*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"#owty\w*", "", text, flags=re.IGNORECASE)
    return " ".join(text.split())  # collapse extra spaces


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


def _channel(clip: dict) -> str:
    return clip.get("channel") or (clip.get("metadata_json") or {}).get("channel", "")


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
    return (clip.get("tt_title") or clip.get("yt_title") or fallback)[:90]


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


# ─── Blotato API ──────────────────────────────────────────────────────────────

def _headers(api_key: str) -> dict:
    return {"blotato-api-key": api_key}


def _upload_video(video_path: str, api_key: str) -> str:
    """Upload a local video file and return the public URL."""
    filename = Path(video_path).name
    log.info("[Blotato] Requesting presigned URL for %s…", filename)

    # Step 1: get presigned URL
    resp = requests.post(
        f"{BLOTATO_BASE}/media/uploads",
        headers={**_headers(api_key), "Content-Type": "application/json"},
        json={"filename": filename},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    presigned_url = data["presignedUrl"]
    public_url    = data["publicUrl"]
    log.info("[Blotato] Uploading video to presigned URL…")

    # Step 2: PUT the binary to S3
    with open(video_path, "rb") as f:
        put_resp = requests.put(
            presigned_url,
            data=f,
            headers={"Content-Type": "video/mp4"},
            timeout=600,
        )
    put_resp.raise_for_status()
    log.info("[Blotato] Upload complete. Public URL: %s", public_url)
    return public_url


def _post_to_platform(account_id: str, public_url: str, platform: str,
                      clip: dict, api_key: str) -> dict:
    """Post to a single platform and return the response."""
    body = {
        "post": {
            "accountId": account_id,
            "content": {
                "text":      _ig_caption(clip),
                "mediaUrls": [public_url],
                "platform":  platform.lower(),
            },
            "target": _build_target(platform, clip),
        }
    }

    log.info("[Blotato] Posting to %s (account %s)…", platform, account_id)
    resp = requests.post(
        f"{BLOTATO_BASE}/posts",
        headers={**_headers(api_key), "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _build_target(platform: str, clip: dict) -> dict:
    if platform == "youtube":
        return {
            "targetType":              "youtube",
            "title":                   _yt_title(clip),
            "privacyStatus":           "public",
            "shouldNotifySubscribers": False,
        }
    if platform == "tiktok":
        return {
            "targetType":      "tiktok",
            "privacyLevel":    "PUBLIC_TO_EVERYONE",
            "disabledComments": False,
            "disabledDuet":    False,
            "disabledStitch":  False,
            "isBrandedContent": False,
            "isYourBrand":     False,
            "isAiGenerated":   False,
        }
    if platform == "instagram":
        return {
            "targetType": "instagram",
            "mediaType":  "reel",
        }
    if platform == "facebook":
        return {
            "targetType": "facebook",
            "pageId":     os.environ.get("FB_PAGE_ID", ""),
            "mediaType":  "reel",
        }
    return {"targetType": platform.lower()}


def publish_clip(clip: dict, platforms: list[str]) -> dict:
    api_key    = os.environ["BLOTATO_API_KEY"]
    video_path = clip["approved_path"]

    # Map platform names to account IDs from .env
    account_ids = {
        "youtube":   os.environ.get("BLOTATO_YT_ACCOUNT_ID", ""),
        "tiktok":    os.environ.get("BLOTATO_TT_ACCOUNT_ID", ""),
        "instagram": os.environ.get("BLOTATO_IG_ACCOUNT_ID", ""),
        "facebook":  os.environ.get("BLOTATO_FB_ACCOUNT_ID", ""),
    }

    # Upload video once, reuse URL for all platforms
    public_url = _upload_video(video_path, api_key)

    results = {}
    for platform in platforms:
        account_id = account_ids.get(platform)
        if not account_id:
            log.warning("[Blotato] No account ID for %s — skipping.", platform)
            results[platform] = {"skipped": "no account ID configured"}
            continue
        try:
            result = _post_to_platform(account_id, public_url, platform, clip, api_key)
            log.info("[Blotato] %s posted successfully.", platform)
            results[platform] = result
        except Exception as exc:
            log.error("[Blotato] %s failed: %s", platform, exc)
            results[platform] = {"error": str(exc)}

    return {
        "platforms":   platforms,
        "public_url":  public_url,
        "status":      "published",
        "detail":      results,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Post approved clips to social platforms via Blotato API."
    )
    parser.add_argument("approved_json",
                        help="Path to approved clips JSON, or '-' for stdin")
    parser.add_argument("--platforms", nargs="+",
                        default=["youtube", "tiktok", "instagram"])
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
            result = {"error": str(exc), "platforms": args.platforms}
        summary.append({
            "clip_index":    clip.get("clip_index"),
            "approved_path": clip.get("approved_path"),
            "result":        result,
        })

    print(json.dumps({"published": summary}, indent=2))


if __name__ == "__main__":
    main()
