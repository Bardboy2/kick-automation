#!/usr/bin/env python3
"""
generate_metadata.py — Claude AI Metadata Generator
Takes captioned clip data (transcript + AI description) and asks Claude to
produce platform-optimised titles, descriptions, and hashtags for
YouTube Shorts, TikTok, and Instagram Reels.

Usage:
    python generate_metadata.py <captioned_json_path_or_stdin>
    python caption.py - | python generate_metadata.py -
"""

import sys
import os
import json
import logging
from pathlib import Path

import anthropic

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / "config" / ".env", override=False)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [metadata] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

CHANNEL_SLUG  = os.environ.get("KICK_CHANNEL_SLUG", "streamer")
IS_OWTY       = CHANNEL_SLUG == "owtyofficial"
PLATFORM      = "Kick" if IS_OWTY else "Twitch"
CONTENT_TYPE  = f"IRL / Just Chatting and Music / DJ live stream on {PLATFORM}"

SYSTEM_PROMPT = f"""\
You are a social media growth expert who writes viral short-form video copy for \
a {CONTENT_TYPE} channel called @{CHANNEL_SLUG}.

Given a clip description and transcript excerpt you will output metadata for THREE \
platforms in one JSON object — no markdown fences, pure JSON only.

Return exactly this shape:
{{
  "youtube": {{
    "title": "<≤100 chars, hook-first, no clickbait>",
    "description": "<2-4 sentences, conversational, include call-to-action>",
    "tags": ["tag1", "tag2", "..."]
  }},
  "tiktok": {{
    "title": "<≤150 chars, punchy, emoji ok>"
  }},
  "instagram": {{
    "caption": "<2-3 lines max, emoji ok, ends with call-to-action>"
  }},
  "hashtags": ["hashtag1", "hashtag2", "..."]
}}

Rules:
- "hashtags" is a shared list (10-15 tags) used across all platforms
- YouTube tags: plain words without #, 10-15 items
- Titles must create curiosity or emotion without misleading
- Content type context: IRL reactions, music drops, funny moments, surprise cuts
- Do NOT mention @owtyofficial or @owtygram unless the channel is owtyofficial
- Do NOT include #kick or #kickstreaming unless the channel is owtyofficial
- Do not hallucinate facts not present in the description/transcript
"""


def build_user_prompt(clip: dict) -> str:
    description = clip.get("ai_description", "")
    transcript  = clip.get("transcript", "")[:500]   # cap at 500 chars
    duration    = clip.get("duration", 0)
    ai_score    = clip.get("ai_score", 5)

    return (
        f"Clip info:\n"
        f"- AI description: {description or 'Not available'}\n"
        f"- Transcript excerpt: {transcript or 'No speech detected'}\n"
        f"- Duration: {duration:.0f} seconds\n"
        f"- Viral score (0-10): {ai_score}\n\n"
        "Generate the metadata JSON now."
    )


def _empty_metadata() -> dict:
    if IS_OWTY:
        return {
            "youtube":   {"title": "Viral moment from Kick", "description": "Caught this live on Kick.", "tags": []},
            "tiktok":    {"title": "Caught this on Kick"},
            "instagram": {"caption": "Caught this live on Kick"},
            "hashtags":  ["#kick", "#kickstreaming", "#viral", "#clips", "#livestream"],
        }
    return {
        "youtube":   {"title": "Viral stream moment", "description": "Caught this live on stream.", "tags": []},
        "tiktok":    {"title": "Caught this on stream"},
        "instagram": {"caption": "Caught this live on stream"},
        "hashtags":  ["#twitch", "#streamclips", "#viral", "#livestream", "#clips"],
    }


def generate_for_clip(clip: dict, client: anthropic.Anthropic) -> dict:
    log.info("Generating metadata for clip %d…", clip.get("clip_index", 0))
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{
                "role":    "user",
                "content": build_user_prompt(clip),
            }],
        )
        raw = msg.content[0].text.strip()
        # Strip markdown fences if Claude wraps output
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = json.loads(raw)
        log.info("  YT title: %s", data.get("youtube", {}).get("title", "")[:60])
        return data
    except Exception as exc:
        log.error("Claude metadata generation failed for clip %d: %s — raw: %r",
                  clip.get("clip_index", 0), exc,
                  locals().get("raw", "")[:200])
        return _empty_metadata()


def generate_all(captioned_data: dict) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — using placeholder metadata.")
        clips_out = [{**c, "metadata": _empty_metadata()}
                     for c in captioned_data.get("clips", [])]
        return {"clips": clips_out, "total": len(clips_out)}

    client    = anthropic.Anthropic(api_key=api_key)
    clips_out = []

    for clip in captioned_data.get("clips", []):
        meta = generate_for_clip(clip, client)
        clips_out.append({**clip, "metadata": meta})

    return {"clips": clips_out, "total": len(clips_out)}


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate AI platform metadata for captioned clips."
    )
    parser.add_argument("captioned_json", help="Path to captioned JSON, or '-' for stdin")
    args = parser.parse_args()

    data = (json.load(sys.stdin) if args.captioned_json == "-"
            else json.loads(Path(args.captioned_json).read_text()))

    result = generate_all(data)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
