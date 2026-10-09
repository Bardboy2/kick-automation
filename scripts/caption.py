#!/usr/bin/env python3
"""
caption.py — Viral Word-by-Word Caption Generator
Transcribes each clip with faster-whisper (word-level timestamps), renders
caption PNGs with Pillow, builds a timed concat sequence, then composites it
onto the video with a SINGLE FFmpeg overlay pass (fast, no libass needed).

Usage:
    python caption.py <cuts_json_path_or_stdin> [--model small] [--output-dir data/captioned]
    python cut_clips.py - | python caption.py -
"""

import sys
import json
import argparse
import logging
import subprocess
import os
import shutil
import tempfile
from pathlib import Path

from faster_whisper import WhisperModel
from PIL import Image, ImageDraw, ImageFont

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / "config" / ".env", override=True)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [caption] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_DIR        = Path(__file__).parent.parent
OUTPUT_DIR      = BASE_DIR / "data" / "captioned"
WORDS_PER_GROUP = 2   # 1 = maximum energy, 2 = balanced readability

FFMPEG  = os.environ.get("FFMPEG_BIN",  "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")

# Caption style
FONT_SIZE     = 88
TEXT_COLOR    = (255, 255, 255, 255)
OUTLINE_COLOR = (0, 0, 0, 255)
OUTLINE_WIDTH = 6


# ─── Whisper transcription ────────────────────────────────────────────────────

_whisper_model_cache: dict = {}


def _get_model(name: str) -> WhisperModel:
    if name not in _whisper_model_cache:
        log.info("Loading faster-whisper model '%s'…", name)
        _whisper_model_cache[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return _whisper_model_cache[name]


def transcribe_to_words(clip_path: str, model_name: str = "small") -> list[dict]:
    """Return flat word list with start/end timestamps, or None if no speech."""
    model = _get_model(model_name)
    log.info("Transcribing %s…", Path(clip_path).name)
    try:
        segments, _ = model.transcribe(clip_path, word_timestamps=True, language="en")
    except Exception as exc:
        log.error("Whisper failed for %s: %s", clip_path, exc)
        return None

    words = []
    for seg in segments:
        for w in (seg.words or []):
            text = w.word.strip()
            if text:
                words.append({"word": text, "start": float(w.start), "end": float(w.end)})

    log.info("Got %d word(s) from %s.", len(words), Path(clip_path).name)
    return words or None


# ─── Video info ───────────────────────────────────────────────────────────────

def get_video_info(clip_path: str) -> tuple[int, int, float]:
    """Return (width, height, duration_seconds)."""
    cmd = [FFPROBE, "-v", "quiet", "-print_format", "json",
           "-show_streams", "-show_format", clip_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr}")
    data = json.loads(r.stdout)
    vs = next(s for s in data["streams"] if s["codec_type"] == "video")
    return int(vs["width"]), int(vs["height"]), float(data["format"]["duration"])


# ─── Pillow PNG helpers ───────────────────────────────────────────────────────

def _get_font(size: int):
    for path in [
        "/Library/Fonts/Arial Black.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Arial.ttf",
    ]:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _make_caption_png(text: str, width: int, height: int, out_path: str) -> None:
    """Transparent RGBA PNG with centred white text + black outline."""
    img  = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _get_font(FONT_SIZE)

    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (width  - tw) // 2 - bbox[0]
    y = (height - th) // 2 - bbox[1]

    for dx in range(-OUTLINE_WIDTH, OUTLINE_WIDTH + 1):
        for dy in range(-OUTLINE_WIDTH, OUTLINE_WIDTH + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=OUTLINE_COLOR)
    draw.text((x, y), text, font=font, fill=TEXT_COLOR)
    img.save(out_path, "PNG")


def _make_transparent_png(width: int, height: int, out_path: str) -> None:
    Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(out_path, "PNG")


# ─── Caption burning — single overlay pass ───────────────────────────────────

def burn_subtitles(clip_path: str, output_path: Path,
                   words: list[dict],
                   words_per_group: int = WORDS_PER_GROUP) -> bool:
    """
    Strategy:
      1. Render each caption group as a transparent PNG (Pillow).
      2. Build a concat sequence file that shows each PNG for exactly the right duration.
      3. Run FFmpeg with the concat sequence as a second input; ONE overlay pass.
      FFmpeg never needs drawtext or libass.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not words:
        shutil.copy2(clip_path, output_path)
        return True

    # Build caption groups
    groups: list[tuple[float, float, str]] = []
    i = 0
    while i < len(words):
        grp   = words[i: i + words_per_group]
        start = grp[0]["start"]
        end   = grp[-1]["end"]
        if end - start < 0.15:
            end = start + 0.15
        text = " ".join(w["word"].strip().upper() for w in grp)
        groups.append((start, end, text))
        i += words_per_group

    if not groups:
        shutil.copy2(clip_path, output_path)
        return True

    try:
        w, h, duration = get_video_info(clip_path)
    except Exception as exc:
        log.error("ffprobe failed: %s", exc)
        return False

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # Create fully-transparent PNG for silence gaps
        transparent = str(tmp / "transparent.png")
        _make_transparent_png(w, h, transparent)

        # Render unique caption images
        png_map: dict[str, str] = {}
        for gi, (_, _, text) in enumerate(groups):
            if text not in png_map:
                png_path = str(tmp / f"cap_{gi:04d}.png")
                _make_caption_png(text, w, h, png_path)
                png_map[text] = png_path

        # Build the concat sequence file
        # Structure: transparent gap → caption → ... → trailing gap
        concat_lines = ["ffconcat version 1.0"]

        def add_segment(path: str, dur: float) -> None:
            if dur > 0.001:
                concat_lines.append(f"file '{path}'")
                concat_lines.append(f"duration {dur:.6f}")

        prev_end = 0.0
        for start, end, text in groups:
            add_segment(transparent, start - prev_end)   # silence before
            add_segment(png_map[text], end - start)      # caption
            prev_end = end

        add_segment(transparent, duration - prev_end)    # trailing silence

        # FFconcat needs a final file entry (no duration) so the last frame holds
        last_file = png_map[groups[-1][2]] if groups else transparent
        concat_lines.append(f"file '{last_file}'")

        concat_path = str(tmp / "captions.txt")
        Path(concat_path).write_text("\n".join(concat_lines))

        # Single FFmpeg pass: clip + caption sequence → one overlay
        cmd = [
            FFMPEG, "-y",
            "-f", "concat", "-safe", "0", "-i", concat_path,  # input 0: caption video
            "-i", clip_path,                                    # input 1: clip
            "-filter_complex", "[1:v][0:v]overlay=0:0[out]",
            "-map", "[out]",
            "-map", "1:a",
            "-c:v", "libx264", "-crf", "23", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(output_path),
        ]

        log.info("Burning %d caption group(s) into %s (single overlay pass)…",
                 len(groups), output_path.name)
        result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        log.error("FFmpeg overlay failed:\n%s", result.stderr[-3000:])
        return False

    log.info("Done: %s (%.1f MB)", output_path.name,
             output_path.stat().st_size / 1e6)
    return True


# ─── Per-clip orchestration ───────────────────────────────────────────────────

def caption_clip(clip: dict, output_dir: Path, model_name: str,
                 words_per_group: int = WORDS_PER_GROUP) -> dict:
    clip_path     = clip["clip_path"]
    idx           = clip["clip_index"]
    source_folder = Path(clip_path).parent.name

    words      = transcribe_to_words(clip_path, model_name)
    transcript = " ".join(w["word"] for w in words) if words else ""

    out_name = Path(clip_path).stem + "_captioned.mp4"
    out_path = output_dir / source_folder / out_name

    if not words:
        log.warning("Clip %d: no speech detected — copying without captions.", idx)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(clip_path, out_path)
        return {**clip, "captioned_path": str(out_path), "transcript": ""}

    if not burn_subtitles(clip_path, out_path, words, words_per_group):
        return None

    return {**clip, "captioned_path": str(out_path), "transcript": transcript}


def caption_all(cuts_data: dict, output_dir: Path, model_name: str,
                words_per_group: int = WORDS_PER_GROUP) -> dict:
    clips_out = []
    for clip in cuts_data.get("clips", []):
        result = caption_clip(clip, output_dir, model_name, words_per_group)
        if result:
            clips_out.append(result)
    return {"clips": clips_out, "total": len(clips_out)}


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Add viral word-by-word captions (Whisper + Pillow + FFmpeg overlay)."
    )
    parser.add_argument("cuts_json", help="Path to cuts JSON, or '-' for stdin")
    parser.add_argument("--model",           default=os.environ.get("WHISPER_MODEL", "small"))
    parser.add_argument("--output-dir",      default=str(OUTPUT_DIR))
    parser.add_argument("--words-per-group", type=int, default=WORDS_PER_GROUP)
    args = parser.parse_args()

    cuts_data = (json.load(sys.stdin) if args.cuts_json == "-"
                 else json.loads(Path(args.cuts_json).read_text()))

    result = caption_all(
        cuts_data,
        Path(args.output_dir),
        model_name=args.model,
        words_per_group=args.words_per_group,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
