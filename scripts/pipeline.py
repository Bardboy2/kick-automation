#!/usr/bin/env python3
"""
pipeline.py — Full Pipeline Orchestrator
Chains all stages for a single VOD:
  1. detect_peaks.py     — audio + Claude AI viral moment detection
  2. cut_clips.py        — FFmpeg clip cutting (landscape)
  3. transcribe.py       — faster-whisper word-level transcription
  4. burn_captions.py    — PIL+FFmpeg karaoke captions → 3 formats (landscape, portrait crop, portrait black bg)
  5. generate_metadata.py — Claude AI titles / descriptions / hashtags
  6. db.py               — saves clips to SQLite for review

After this script finishes, open the caption editor to review and approve:
  python scripts/caption_editor.py   →   http://localhost:8888

Usage:
    python pipeline.py --vod-id <id> --vod-path /path/to/source.mp4
    python pipeline.py --trigger-file data/triggers/<id>.json
"""

import sys
import os
import json
import argparse
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / "config" / ".env", override=False)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [pipeline] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_DIR    = Path(__file__).parent.parent
SCRIPTS_DIR = Path(__file__).parent
LOG_DIR     = BASE_DIR / "data" / "logs"

# Pipeline tuning — can be overridden via environment variables
CLIPS_PER_VOD     = int(os.environ.get("CLIPS_PER_VOD",     "3"))
MIN_GAP_SEC       = float(os.environ.get("MIN_GAP_SEC",     "60"))
CLIP_DURATION_SEC = float(os.environ.get("CLIP_DURATION_SEC","45"))


def run_stage(name: str, *args, stdin_data=None) -> dict:
    """Run a pipeline stage script, optionally piping JSON in via stdin."""
    script = SCRIPTS_DIR / name
    cmd    = [sys.executable, str(script), *[str(a) for a in args]]
    if stdin_data is not None:
        cmd.append("-")

    log.info("▶ Stage: %s", name)
    proc = subprocess.run(
        cmd,
        input=json.dumps(stdin_data) if stdin_data is not None else None,
        capture_output=True,
        text=True,
    )

    if proc.returncode != 0:
        log.error("%s failed:\n%s", name, proc.stderr[-3000:])
        raise RuntimeError(f"{name} exited with code {proc.returncode}")

    # Forward stage logs to the pipeline logger
    for line in (proc.stderr or "").strip().splitlines():
        log.debug("[%s] %s", name, line)

    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def run_pipeline(vod_id: str, vod_path: str, vod_title: str = "", landscape: bool = True, watermark: bool = True) -> dict:
    import db  # local import so the module path resolves correctly

    start_time = datetime.now(timezone.utc)
    log.info("═══ Pipeline start: VOD %s ═══", vod_id)

    # Register VOD in DB
    channel = os.environ.get("KICK_CHANNEL_SLUG", "unknown")
    db.upsert_vod(vod_id, vod_title or vod_id, channel, vod_path, status="processing")

    # ── Stage 1: Detect viral moments ─────────────────────────────────────────
    peaks = run_stage(
        "detect_peaks.py",
        vod_path,
        "--top",           CLIPS_PER_VOD,
        "--min-gap",       MIN_GAP_SEC,
        "--clip-duration", CLIP_DURATION_SEC,
    )
    log.info("Stage 1 done: %d peak(s) found.", peaks.get("peaks_found", 0))
    if not peaks.get("clips"):
        log.warning("No peaks — pipeline halted for VOD %s.", vod_id)
        db.update_vod_status(vod_id, "no_peaks")
        return {"status": "no_peaks", "vod_id": vod_id}

    # ── Stage 2: Cut clips (landscape) ───────────────────────────────────────
    cut_args = ["--landscape"]   # always cut in landscape; burn_captions makes portrait
    if not watermark:
        cut_args.append("--no-watermark")
    cuts = run_stage("cut_clips.py", *cut_args, stdin_data=peaks)
    log.info("Stage 2 done: %d clip(s) cut.", cuts.get("total", 0))
    if not cuts.get("clips"):
        db.update_vod_status(vod_id, "no_clips")
        return {"status": "no_clips", "vod_id": vod_id}

    # ── Stage 3: Word-level transcription ─────────────────────────────────────
    try:
        transcribed = run_stage("transcribe.py", stdin_data=cuts)
        log.info("Stage 3 done: transcription complete.")
    except Exception as e:
        log.warning("Transcription failed (%s) — continuing without captions.", e)
        transcribed = cuts

    # ── Stage 4: Burn captions → 3 video formats ─────────────────────────────
    try:
        captioned = run_stage("burn_captions.py", stdin_data=transcribed)
        log.info("Stage 4 done: captions burned.")
    except Exception as e:
        log.warning("Caption burn failed (%s) — continuing with un-captioned clips.", e)
        captioned = transcribed

    # ── Stage 5: AI metadata (titles / descriptions / hashtags) ──────────────
    enriched = run_stage("generate_metadata.py", stdin_data=captioned)
    log.info("Stage 5 done: metadata generated for %d clip(s).", enriched.get("total", 0))

    # ── Stage 6: Save to SQLite for dashboard review ──────────────────────────
    for clip in enriched.get("clips", []):
        clip.setdefault("captioned_path",
                        clip.get("landscape_path") or clip["clip_path"])

    clips = enriched.get("clips", []) or captioned.get("clips", [])
    saved_ids = []
    for clip in clips:
        row_id = db.insert_clip(clip, vod_id)
        saved_ids.append(row_id)
        log.info("Clip %d saved to DB (row id %d, status=pending).",
                 clip.get("clip_index", 0), row_id)

    db.update_vod_status(vod_id, "awaiting_review")

    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    log.info("═══ Pipeline done in %.0fs — %d clip(s) awaiting review ═══",
             elapsed, len(saved_ids))
    log.info("Open caption editor:  python scripts/caption_editor.py")

    result = {
        "status":    "awaiting_review",
        "vod_id":    vod_id,
        "elapsed_s": round(elapsed, 1),
        "clip_ids":  saved_ids,
        "clips":     len(saved_ids),
    }

    # Write summary log
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts       = start_time.strftime("%Y%m%dT%H%M%S")
    log_file = LOG_DIR / f"{vod_id}_{ts}.json"
    log_file.write_text(json.dumps(result, indent=2))

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Run the full viral clip pipeline for a VOD."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--vod-path",     help="Path to the downloaded VOD file")
    group.add_argument("--trigger-file", help="Trigger JSON file written by monitor.py")
    parser.add_argument("--vod-id",    help="VOD identifier (inferred from path if omitted)")
    parser.add_argument("--vod-title", help="VOD title (optional, for display)")
    parser.add_argument("--channel",      help="Kick channel slug / watermark handle")
    parser.add_argument("--landscape",    action="store_true", help="Keep clips in landscape (skip 9:16 crop)")
    parser.add_argument("--no-watermark", action="store_true", help="Skip watermark overlay")
    args = parser.parse_args()

    if args.trigger_file:
        trigger   = json.loads(Path(args.trigger_file).read_text())
        vod_id    = trigger["vod_id"]
        vod_path  = trigger["local_path"]
        vod_title = trigger.get("title", "")
    else:
        vod_path  = args.vod_path
        vod_id    = args.vod_id or Path(vod_path).parent.name
        vod_title = args.vod_title or ""

    if getattr(args, "channel", None):
        os.environ["KICK_CHANNEL_SLUG"] = args.channel

    result = run_pipeline(vod_id, vod_path, vod_title,
                          landscape=getattr(args, "landscape", False),
                          watermark=not getattr(args, "no_watermark", False))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
