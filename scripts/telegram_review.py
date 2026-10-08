#!/usr/bin/env python3
"""
telegram_review.py — Telegram Preview & Approval Bot
Sends each captioned clip to your Telegram as a video with Approve / Skip buttons.
Waits for your response before passing approved clips to the publisher.

Usage:
    python telegram_review.py <captioned_json_path_or_stdin> [--output /data/approved]

Environment variables required:
    TELEGRAM_BOT_TOKEN   — from @BotFather
    TELEGRAM_CHAT_ID     — your personal chat ID (get it from @userinfobot)
"""

import sys
import os
import json
import time
import logging
import asyncio
from pathlib import Path

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [telegram] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

BOT_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID     = int(os.environ["TELEGRAM_CHAT_ID"])
OUTPUT_DIR  = Path(os.environ.get("APPROVED_DIR", "/data/approved"))
TIMEOUT_SEC = int(os.environ.get("REVIEW_TIMEOUT", "3600"))  # 1 hour default


# ─── Telegram helpers ─────────────────────────────────────────────────────────

def build_keyboard(clip_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅  Approve", callback_data=f"approve:{clip_id}"),
        InlineKeyboardButton("❌  Skip",    callback_data=f"skip:{clip_id}"),
    ]])


def caption_text(clip: dict) -> str:
    idx        = clip["clip_index"]
    score      = clip["score"]
    duration   = clip["duration"]
    peak_time  = clip["peak_time"]
    video_path = clip.get("video_path", "")
    vod_id     = Path(video_path).parent.name if video_path else "?"

    minutes = int(peak_time // 60)
    seconds = int(peak_time % 60)

    return (
        f"🎬 *Clip {idx + 1}* from VOD `{vod_id}`\n"
        f"⚡ Hype score: *{score:.0%}*\n"
        f"⏱ Peak at {minutes}m{seconds:02d}s  •  {duration:.0f}s long\n\n"
        "Approve to post to YouTube Shorts, TikTok & Instagram Reels."
    )


# ─── Review loop ──────────────────────────────────────────────────────────────

class ReviewSession:
    """Manages sending clips and collecting approve/skip decisions."""

    def __init__(self, clips: list[dict], bot: Bot):
        self.clips     = clips
        self.bot       = bot
        self.decisions = {}          # clip_id → "approve" | "skip"
        self.msg_to_clip = {}        # message_id → clip_id

    async def send_clip(self, clip: dict) -> int:
        """Send a clip video to Telegram and return the message_id."""
        clip_id   = str(clip["clip_index"])
        clip_path = clip["captioned_path"]

        log.info("Sending clip %s to Telegram…", clip_id)
        with open(clip_path, "rb") as f:
            msg = await self.bot.send_video(
                chat_id     = CHAT_ID,
                video       = f,
                caption     = caption_text(clip),
                parse_mode  = "Markdown",
                reply_markup= build_keyboard(clip_id),
                supports_streaming=True,
            )
        self.msg_to_clip[msg.message_id] = clip_id
        log.info("Sent clip %s (message_id=%d)", clip_id, msg.message_id)
        return msg.message_id

    async def send_all(self):
        for clip in self.clips:
            await self.send_clip(clip)
            await asyncio.sleep(2)   # avoid flood limits

    def record(self, clip_id: str, decision: str):
        self.decisions[clip_id] = decision
        log.info("Clip %s → %s", clip_id, decision)

    def all_decided(self) -> bool:
        return all(str(c["clip_index"]) in self.decisions for c in self.clips)

    def approved_clips(self) -> list[dict]:
        return [
            c for c in self.clips
            if self.decisions.get(str(c["clip_index"])) == "approve"
        ]


# ─── Application callbacks ─────────────────────────────────────────────────────

_session: ReviewSession = None


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data  # e.g. "approve:0"
    action, clip_id = data.split(":", 1)

    if _session:
        _session.record(clip_id, action)

    label = "✅ Approved!" if action == "approve" else "❌ Skipped"
    await query.edit_message_reply_markup(reply_markup=None)
    await query.edit_message_caption(
        caption=query.message.caption + f"\n\n{label}",
        parse_mode="Markdown",
    )


# ─── Entrypoint ───────────────────────────────────────────────────────────────

async def run_review(clips: list[dict]) -> list[dict]:
    global _session

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(button_callback))

    async with app:
        bot      = app.bot
        _session = ReviewSession(clips, bot)

        await app.start()
        await _session.send_all()

        log.info("Waiting for approval decisions (timeout %ds)…", TIMEOUT_SEC)
        deadline = time.time() + TIMEOUT_SEC
        while not _session.all_decided():
            if time.time() > deadline:
                log.warning("Timeout reached — treating undecided clips as skipped.")
                break
            await asyncio.sleep(3)

        approved = _session.approved_clips()
        await app.stop()

    return approved


def save_approved(clips: list[dict]) -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for clip in clips:
        src  = Path(clip["captioned_path"])
        dest = OUTPUT_DIR / src.name
        if not dest.exists():
            import shutil
            shutil.copy2(src, dest)
        saved.append({**clip, "approved_path": str(dest)})

    out_file = OUTPUT_DIR / "approved.json"
    out_file.write_text(json.dumps({"clips": saved}, indent=2))
    log.info("Saved %d approved clip(s) to %s", len(saved), OUTPUT_DIR)
    return {"clips": saved, "total": len(saved)}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Telegram review bot for clip approval.")
    parser.add_argument("captioned_json", help="Path to captioned clips JSON, or '-' for stdin")
    parser.add_argument("--output-dir",   default=str(OUTPUT_DIR))
    args = parser.parse_args()

    global OUTPUT_DIR
    OUTPUT_DIR = Path(args.output_dir)

    if args.captioned_json == "-":
        data = json.load(sys.stdin)
    else:
        data = json.loads(Path(args.captioned_json).read_text())

    clips    = data.get("clips", [])
    approved = asyncio.run(run_review(clips))
    result   = save_approved(approved)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
